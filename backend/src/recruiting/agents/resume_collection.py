"""Agent 9 - Resume Collection (BRD Agent 9, plan section 9).

When a candidate responds positively, Agent 9 drafts an email (recruiter approval gate) with a
secure, expiring link. Through that link, or by replying, the candidate supplies the resume, the
seven BRD details and consent: to store their data, and to be submitted for this specific job
(compliance checklist P1, P2). Once everything is in, the engagement is COMPLETE and the candidate
moves to matching (Agent 10). Missing items carry a reminder date for Agent 12.

Only a hash of the upload token is stored on the engagement.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from recruiting.agents.client_outreach import WorkflowStarter
from recruiting.agents.jd_intake import InboundEmail
from recruiting.agents.resume_intake import ResumeFile, ResumeIntake
from recruiting.compliance import OptOutRegistry
from recruiting.compliance.unsubscribe import UnsubscribeLinks, compliance_footer
from recruiting.domain import Channel, GateType
from recruiting.outreach import DeliveryLog
from recruiting.records.engagements import EngagementStore
from recruiting.records.memory import CandidateRepository, JobRepository
from recruiting.records.models import (
    DETAIL_ITEMS,
    Candidate,
    ConsentStatus,
    Engagement,
    EngagementStatus,
    Job,
)

LABELS = {
    "resume": "Your current resume",
    "availability": "When you could start",
    "visa_status": "Visa status",
    "current_location": "Current location",
    "expected_rate": "Expected rate",
    "notice_period": "Notice period",
    "work_authorization": "Work authorisation",
    "preferred_location": "Preferred location",
    "consent": "Your consent to store your details and submit you for this role",
}
REQUESTABLE = {
    EngagementStatus.CONTACTED, EngagementStatus.ACCEPTED, EngagementStatus.REPLIED,
    EngagementStatus.INTERESTED, EngagementStatus.DETAILS_REQUESTED,
}  # fmt: skip


class CollectionError(Exception):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class CollectedDetails:
    resume_text: str | None = None
    resume_file: ResumeFile | None = None  # the uploaded original, when a file was sent
    availability: str | None = None
    visa_status: str | None = None
    current_location: str | None = None
    expected_rate: str | None = None
    notice_period: str | None = None
    work_authorization: str | None = None
    preferred_location: str | None = None
    consent_store: bool = False
    consent_submit: bool = False


@dataclass
class ReplyResult:
    candidate_id: str
    engagement_id: str | None
    intent: str
    updated: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    details_request_id: str | None = None


# ----- reply reading (rule-based; candidate replies are short and structured) ----------------


def _any_of(*phrases: str) -> re.Pattern[str]:
    return re.compile("|".join(rf"\b{p}\b" for p in phrases), re.I)


_INTENTS = [
    ("unsubscribe", _any_of("unsubscribe", "remove me", r"stop (emailing|contacting)")),
    ("not_interested", _any_of("not interested", "not looking", r"no thanks?", "happy where i am")),
    ("interested", _any_of(
        "interested", r"sounds (good|great)", r"happy to (talk|chat)", r"let'?s (talk|chat)", "yes"
    )),
]  # fmt: skip
_RATE = re.compile(
    r"\$\s?\d[\d,.]*\s*(?:k\b|/\s?h(?:ou)?r|\s?per hour|/\s?year|\s?per year|\s?c2c|\s?w2)?", re.I
)
_NOTICE = re.compile(
    r"(\d+\s*(?:day|week|month)s?)\s*(?:'s)?\s*notice"
    r"|notice(?: period)?(?: is|:)?\s*(\d+\s*(?:day|week|month)s?)",
    re.I,
)
_AVAILABLE = re.compile(
    r"(?:available|start)\s*(?:to start\s*)?"
    r"(immediately|now|in\s+\d+\s*(?:day|week|month)s?|from\s+[\w ,]+?\d{1,2})",
    re.I,
)
_AUTH = re.compile(
    r"\b(us citizen|u\.s\. citizen|green card(?: holder)?|permanent resident"
    r"|h-?1b(?: transfer)?|tn visa|opt|ead)\b",
    re.I,
)
_CONSENT = re.compile(r"\bi (consent|agree)\b", re.I)


def read_reply(text: str) -> tuple[str, dict[str, str], bool]:
    intent = next((name for name, pattern in _INTENTS if pattern.search(text)), "replied")
    found: dict[str, str] = {}
    if m := _RATE.search(text):
        found["expected_rate"] = m.group(0).strip()
    if m := _NOTICE.search(text):
        found["notice_period"] = (m.group(1) or m.group(2)).strip()
    if m := _AVAILABLE.search(text):
        found["availability"] = m.group(1).strip()
    if m := _AUTH.search(text):
        found["work_authorization"] = m.group(1)
        found["visa_status"] = m.group(1)
    return intent, found, bool(_CONSENT.search(text))


class ResumeCollectionAgent:
    def __init__(
        self,
        candidates: CandidateRepository,
        jobs: JobRepository,
        engagements: EngagementStore,
        intake: ResumeIntake,
        workflows: WorkflowStarter | None,
        opt_outs: OptOutRegistry,
        deliveries: DeliveryLog,
        unsubscribe: UnsubscribeLinks,
        public_base_url: str,
        postal_address: str,
        link_valid_days: int = 14,
        remind_after_days: int = 3,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        refresh: Callable[[Engagement], Engagement] | None = None,
    ) -> None:
        # Settles a drafted engagement from its approvals and deliveries (Agent 8's refresh), so
        # a reply is read against the current status even if nobody has opened the job since.
        self._refresh = refresh or (lambda e: e)
        self._candidates = candidates
        self._jobs = jobs
        self._engagements = engagements
        self._intake = intake
        self._workflows = workflows
        self._opt_outs = opt_outs
        self._deliveries = deliveries
        self._unsubscribe = unsubscribe
        self._base_url = public_base_url.rstrip("/")
        self._postal_address = postal_address
        self._valid = timedelta(days=link_valid_days)
        self._remind = timedelta(days=remind_after_days)
        self._clock = clock

    # ----- completeness ------------------------------------------------------------------------

    @staticmethod
    def missing_items(engagement: Engagement, candidate: Candidate) -> list[str]:
        missing = [] if (candidate.resume_text or "").strip() else ["resume"]
        missing += [item for item in DETAIL_ITEMS if not (getattr(candidate, item) or "").strip()]
        if not (engagement.consent_store_at and engagement.consent_submit_at):
            missing.append("consent")
        return missing

    def _update_status(self, engagement: Engagement, candidate: Candidate) -> Engagement:
        now = self._clock()
        engagement.missing = self.missing_items(engagement, candidate)
        if not engagement.missing:
            if engagement.status is not EngagementStatus.COMPLETE:
                engagement.log(now, "complete")
            engagement.status = EngagementStatus.COMPLETE
            engagement.next_reminder_at = None
        elif engagement.status is EngagementStatus.DETAILS_REQUESTED:
            engagement.next_reminder_at = now + self._remind
        return engagement

    # ----- request -------------------------------------------------------------------------------

    def request(self, engagement_id: str, recruiter: str) -> tuple[Engagement, str]:
        """Draft the request email (approval gate). Returns the engagement and the upload link,
        which the recruiter can also share in an existing LinkedIn conversation."""
        engagement = self._engagements.get(engagement_id)
        if engagement is None:
            raise CollectionError("Unknown engagement")
        if engagement.status not in REQUESTABLE:
            raise CollectionError(
                f"Cannot request details while the candidate is {engagement.status}"
            )
        candidate = self._candidates.get(engagement.candidate_id)
        job = self._jobs.get(engagement.job_id)
        if candidate is None or job is None:
            raise CollectionError("Candidate or job no longer exists")
        if any(
            self._opt_outs.is_opted_out(a) for a in (candidate.email, candidate.linkedin_url) if a
        ):
            raise CollectionError("The candidate has opted out")

        now = self._clock()
        token = secrets.token_urlsafe(32)
        link = f"{self._base_url}/collect/{token}"
        engagement.token_hash = token_hash(token)
        engagement.token_expires_at = now + self._valid
        engagement.requested_at = now
        engagement.status = EngagementStatus.DETAILS_REQUESTED
        engagement.missing = self.missing_items(engagement, candidate)
        engagement.next_reminder_at = now + self._remind
        if candidate.email and self._workflows is not None:
            needed = "\n".join(f"  - {LABELS[item]}" for item in engagement.missing)
            footer = compliance_footer(
                recruiter, self._postal_address, self._unsubscribe.url(candidate.email)
            )
            body = (
                f"Hi {candidate.full_name.split()[0]},\n\n"
                f"Thank you for your interest in the {job.title} role. "
                "To put you forward, I need:\n"
                f"{needed}\n\n"
                f"You can share them securely here (the link works until "
                f"{engagement.token_expires_at:%d %B %Y}):\n{link}\n\n"
                "Or simply reply to this email with the details and your resume attached.\n\n"
                f"Regards,\n{recruiter}\nCBS LLC"
            )
            engagement.approvals["details_request"] = self._workflows.start(
                gate=GateType.CANDIDATE_OUTREACH_EMAIL,
                subject_id=candidate.id,
                channel=Channel.EMAIL,
                recipient=candidate.email,
                content={"subject": f"Next steps: {job.title}", "body": body + footer},
                requested_by="agent-9",
            )
        engagement.log(now, "details_requested", by=recruiter)
        self._engagements.save(engagement)
        return engagement, link

    # ----- secure upload link --------------------------------------------------------------------

    def open(self, token: str) -> tuple[Engagement, Candidate, Job]:
        engagement = self._engagements.by_token_hash(token_hash(token))
        if engagement is None:
            raise CollectionError("This link is not valid")
        if engagement.token_expires_at and engagement.token_expires_at < self._clock():
            raise CollectionError("This link has expired; please ask your recruiter for a new one")
        candidate = self._candidates.get(engagement.candidate_id)
        job = self._jobs.get(engagement.job_id)
        if candidate is None or job is None:
            raise CollectionError("This link is no longer active")
        return engagement, candidate, job

    def submit(self, token: str, details: CollectedDetails) -> Engagement:
        engagement, candidate, job = self.open(token)
        now = self._clock()
        if details.resume_text and details.resume_text.strip():
            candidate = self._intake.add(
                details.resume_text, "candidate_upload", candidate_id=candidate.id,
                file=details.resume_file,
            ).candidate  # fmt: skip
        changed = []
        for item in DETAIL_ITEMS:
            value = (getattr(details, item) or "").strip()
            if value:
                setattr(candidate, item, value[:300])
                changed.append(item)
        if details.consent_store and details.consent_submit:
            engagement.consent_store_at = engagement.consent_submit_at = now
            engagement.consent_evidence = (
                f"Ticked both consent boxes on the secure link for {job.job_code}"
                f" at {now.isoformat()}"
            )
            candidate.consent_status = ConsentStatus.GRANTED
        self._candidates.save(candidate)
        engagement.log(
            now,
            "details_received",
            via="secure_link",
            items=changed + (["resume"] if details.resume_text else []),
        )
        self._update_status(engagement, candidate)
        self._engagements.save(engagement)
        return engagement

    # ----- email replies -------------------------------------------------------------------------

    def process_reply(
        self, email: InboundEmail, candidate: Candidate, recruiter: str = "CBS recruiting team"
    ) -> ReplyResult:
        now = email.received_at
        delivery = self._deliveries.latest_to(email.from_address)
        if delivery is not None:
            self._deliveries.record_reply(delivery.message.approval_request_id, now)
        engagements = [
            e
            for e in map(self._refresh, self._engagements.for_candidate(candidate.id))
            if e.status is not EngagementStatus.DRAFT_REJECTED
        ]
        engagement = engagements[0] if engagements else None
        intent, found, consented = read_reply(f"{email.subject}\n{email.body}")
        result = ReplyResult(candidate.id, engagement.id if engagement else None, intent)

        if intent == "unsubscribe":
            for address in (candidate.email, candidate.linkedin_url, email.from_address):
                if address:
                    self._opt_outs.record(address, source=f"reply:{email.message_id}")
            if engagement:
                engagement.status = EngagementStatus.DO_NOT_CONTACT
                engagement.log(now, "unsubscribed")
                self._engagements.save(engagement)
            return result

        if email.attachments_text.strip():
            candidate = self._intake.add(
                email.attachments_text, "candidate_reply", candidate_id=candidate.id
            ).candidate
            result.updated.append("resume")
        for item, value in found.items():
            if not (getattr(candidate, item) or "").strip():
                setattr(candidate, item, value)
                result.updated.append(item)
        self._candidates.save(candidate)
        if engagement is None:
            return result

        if consented and engagement.status in REQUESTABLE:
            engagement.consent_store_at = engagement.consent_submit_at = now
            engagement.consent_evidence = f"Email reply {email.message_id}: written consent"
            result.updated.append("consent")
        if intent == "not_interested":
            engagement.status = EngagementStatus.NOT_INTERESTED
        elif intent == "interested" and engagement.status in (
            EngagementStatus.CONTACTED,
            EngagementStatus.ACCEPTED,
            EngagementStatus.REPLIED,
        ):
            engagement.status = EngagementStatus.INTERESTED
        elif engagement.status in (EngagementStatus.CONTACTED, EngagementStatus.ACCEPTED):
            engagement.status = EngagementStatus.REPLIED
        engagement.log(now, "reply", intent=intent, updated=result.updated)
        self._update_status(engagement, candidate)
        self._engagements.save(engagement)

        if engagement.status is EngagementStatus.INTERESTED:
            engagement, _ = self.request(engagement.id, recruiter)
            result.details_request_id = engagement.approvals.get("details_request")
        result.missing = engagement.missing
        return result

    # ----- reporting -----------------------------------------------------------------------------

    def summary(self, job_id: str) -> dict[str, int | float | None]:
        items = self._engagements.list(job_id=job_id)
        counts = {status.value: 0 for status in EngagementStatus}
        for e in items:
            counts[e.status.value] += 1
        interested = sum(counts[s] for s in ("interested", "details_requested", "complete"))
        return {
            **counts,
            "total": len(items),
            # Phase 5 exit criterion: resume and details for >= 90% of interested candidates.
            "completion_rate": round(counts["complete"] / interested, 3) if interested else None,
        }
