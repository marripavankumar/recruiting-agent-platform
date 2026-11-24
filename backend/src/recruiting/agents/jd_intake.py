"""Agent 4 - JD Intake (BRD Agent 4, plan section 7).

Handles an inbound reply to client outreach: links it to the contact and the email it answers,
classifies it, honours unsubscribes across every channel, and turns job descriptions into Job
Database records (one per position) for the Account Manager to confirm. Each new job goes
straight to Agent 5 for analysis.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from recruiting.agents.jd_analysis import JDAnalysisAgent
from recruiting.ai.llm import LLMError, LLMGateway
from recruiting.ai.prompts import ExtractedJob, JDExtraction, ReplyClassification
from recruiting.ai.skills import extract_skills
from recruiting.compliance import OptOutRegistry
from recruiting.outreach import DeliveryLog
from recruiting.records.memory import CompanyRepository, JobRepository, new_id
from recruiting.records.models import Job, OutreachStatus, Priority

ALL_FIELDS = [
    "responsibilities", "location", "experience", "visa_status", "education", "industry", "travel",
    "work_mode", "salary", "contract_type", "priority",
]  # fmt: skip


@dataclass(frozen=True)
class InboundEmail:
    from_address: str
    subject: str
    body: str
    received_at: datetime
    message_id: str
    attachments_text: str = ""  # text already extracted from PDF / DOCX attachments
    # Marked by its headers as an automatic reply (Auto-Submitted, X-Autoreply ...): S9.
    auto_reply: bool = False


@dataclass
class IntakeResult:
    category: str
    summary: str
    company_id: str | None = None
    contact_id: str | None = None
    approval_request_id: str | None = None  # the outreach email this reply answers
    job_ids: list[str] = field(default_factory=list)
    classified_by: str = "rules"


def _any_of(*phrases: str) -> re.Pattern[str]:
    return re.compile("|".join(rf"\b{p}\b" for p in phrases), re.I)


# Checked in order, after the unsubscribe and JD checks in classify_by_rules.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "out_of_office",
        _any_of(r"out of (the )?office", "automatic reply", r"auto.?reply", "on leave"),
    ),
    ("not_interested", _any_of("not interested", r"no (need|thanks|thank you)", "not looking")),
    (
        "jd_to_follow",
        _any_of(r"(will|shall|going to) (send|share|forward)", r"send (it|them) over"),
    ),
    (
        "not_now",
        _any_of(r"not (right )?now", "maybe later", r"next (quarter|year)", "hiring freeze"),
    ),
    ("interested", _any_of("interested", r"let'?s (talk|connect|schedule)", "call me")),
]
_UNSUBSCRIBE = _any_of("unsubscribe", "remove me", r"stop (emailing|contacting)", r"opt.?out")
_JD_SIGNS = _any_of(
    "job description", "responsibilities", "requirements", "qualifications", "must have",
    "required skills",
)  # fmt: skip


def classify_by_rules(email: InboundEmail) -> ReplyClassification:
    text = f"{email.subject}\n{email.body}"
    # An explicit opt-out wins over everything else, even when a JD is attached.
    if _UNSUBSCRIBE.search(text):
        return ReplyClassification(category="unsubscribe", summary="Asked to stop receiving emails")
    if email.attachments_text.strip() or (len(_JD_SIGNS.findall(text)) >= 2 and len(text) > 300):
        return ReplyClassification(
            category="jd_received", summary="Reply contains a job description"
        )
    for category, pattern in _RULES:
        if pattern.search(text):
            return ReplyClassification(
                category=category, summary=f"Matched {category.replace('_', ' ')} wording"
            )
    return ReplyClassification(
        category="other", summary="No clear intent; needs a person to read it"
    )


def extract_by_rules(email: InboundEmail) -> JDExtraction:
    """Fallback without an LLM: one job, title from the subject, skills from the dictionary."""
    text = f"{email.body}\n{email.attachments_text}"
    subject = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", email.subject, flags=re.I).strip()
    title_line = next(
        (
            line.strip()
            for line in text.splitlines()
            if re.search(r"\bengineer|manager\b", line, re.I)
        ),
        "",
    )
    title = title_line[:120] or subject or "Untitled position"
    skills = [m.skill for m in extract_skills(text)]
    empty = dict.fromkeys(ALL_FIELDS)
    job = ExtractedJob(
        **empty,
        title=title,
        required_skills=skills,
        preferred_skills=[],
        low_confidence_fields=["title", *ALL_FIELDS],
    )
    return JDExtraction(jobs=[job])


class JDIntakeAgent:
    def __init__(
        self,
        companies: CompanyRepository,
        jobs: JobRepository,
        opt_outs: OptOutRegistry,
        deliveries: DeliveryLog,
        analysis: JDAnalysisAgent,
        assign_recruiter: Callable[[], str | None] = lambda: None,
        gateway: LLMGateway | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._companies = companies
        self._jobs = jobs
        self._opt_outs = opt_outs
        self._deliveries = deliveries
        self._analysis = analysis
        self._assign_recruiter = assign_recruiter
        self._gateway = gateway
        self._on_error = on_error or (lambda _key, _exc: None)
        self._clock = clock

    def _classify(self, email: InboundEmail) -> tuple[ReplyClassification, str]:
        if email.auto_reply:
            summary = "Automatic reply (marked by its email headers)"
            return ReplyClassification(category="out_of_office", summary=summary), "headers"
        if self._gateway is not None:
            try:
                result = self._gateway.run(
                    "reply_classification",
                    ReplyClassification,
                    subject=email.subject,
                    body=email.body,
                    attachments=email.attachments_text or "(none)",
                )
                return result, "llm"
            except LLMError as exc:
                self._on_error(email.message_id, exc)
        return classify_by_rules(email), "rules"

    def _extract(self, email: InboundEmail) -> JDExtraction:
        if self._gateway is not None:
            try:
                return self._gateway.run(
                    "jd_extraction",
                    JDExtraction,
                    subject=email.subject,
                    body=email.body,
                    attachments=email.attachments_text or "(none)",
                )
            except LLMError as exc:
                self._on_error(email.message_id, exc)
        return extract_by_rules(email)

    def process(self, email: InboundEmail) -> IntakeResult:
        contact = self._companies.contact_by_email(email.from_address)
        company = self._companies.get(contact.company_id) if contact else None
        delivery = self._deliveries.latest_to(email.from_address)

        classification, by = self._classify(email)
        # S9: an automatic out-of-office reply is not a response, so the email still counts as
        # unanswered and Agent 12 keeps its follow-up reminder (and the response-rate KPI).
        if delivery is not None and classification.category != "out_of_office":
            self._deliveries.record_reply(delivery.message.approval_request_id, email.received_at)
        result = IntakeResult(
            category=classification.category,
            summary=classification.summary,
            company_id=company.id if company else None,
            contact_id=contact.id if contact else None,
            approval_request_id=delivery.message.approval_request_id if delivery else None,
            classified_by=by,
        )

        if classification.category == "unsubscribe":
            self._opt_outs.record(email.from_address, source=f"reply:{email.message_id}")
            if company:
                self._companies.set_outreach_status(company.id, OutreachStatus.DO_NOT_CONTACT)
            return result
        if company and classification.category != "out_of_office":
            if company.outreach_status in (OutreachStatus.NOT_CONTACTED, OutreachStatus.CONTACTED):
                self._companies.set_outreach_status(company.id, OutreachStatus.REPLIED)
        if company and classification.category == "jd_to_follow":
            # S9: no job yet; Agent 12 reminds the BD Manager until the JD arrives.
            company = self._companies.get(company.id) or company
            company.jd_promised_at = company.jd_promised_at or email.received_at
            self._companies.save(company)
            return result
        if classification.category != "jd_received" or company is None:
            # A JD from an unknown sender waits for a person to link it to a company.
            return result

        for extracted in self._extract(email).jobs:
            job = self._create_job(extracted, company.id, company.industry, email)
            result.job_ids.append(job.id)
        company = self._companies.get(company.id) or company
        company.is_client = True
        company.outreach_status = OutreachStatus.CLIENT
        company.jd_promised_at = None
        self._companies.save(company)
        return result

    def _create_job(
        self, extracted: ExtractedJob, company_id: str, industry: str | None, email: InboundEmail
    ) -> Job:
        job = Job(
            id=new_id(),
            job_code=self._jobs.next_job_code(email.received_at.year),
            client_company_id=company_id,
            title=extracted.title,
            priority=Priority(extracted.priority or "medium"),
            recruiter_id=self._assign_recruiter(),
            responsibilities=extracted.responsibilities,
            required_skills=extracted.required_skills,
            preferred_skills=extracted.preferred_skills,
            location=extracted.location,
            experience=extracted.experience,
            visa_requirements=extracted.visa_status,
            education=extracted.education,
            industry=extracted.industry or industry,
            travel=extracted.travel,
            work_mode=extracted.work_mode,
            salary=extracted.salary,
            contract_type=extracted.contract_type,
            source_email_ref=email.message_id,
            review_fields=[
                "visa_requirements" if f == "visa_status" else f
                for f in extracted.low_confidence_fields
            ],
            created_at=self._clock(),
        )
        raw = f"{email.body}\n{email.attachments_text}"
        job = self._analysis.analyse(job, raw)
        self._jobs.add(job)
        return job
