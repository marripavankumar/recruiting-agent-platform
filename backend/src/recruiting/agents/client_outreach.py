"""Agent 3 - Email Outreach (BRD Agent 3, plan section 6).

For each prospective company with a strong hiring signal, picks the best contact, drafts a
personalised first email and starts an outreach workflow, which pauses at the "first client
email" approval gate. Nothing is sent until the BD Manager approves (BRD section 10).

Before drafting, every contact is checked against the opt-out registry and, for Canadian
recipients, for a recorded CASL consent basis.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from recruiting.ai.llm import LLMError, LLMGateway
from recruiting.ai.prompts import ClientEmailDraft
from recruiting.compliance import OptOutRegistry
from recruiting.compliance.unsubscribe import UnsubscribeLinks, compliance_footer
from recruiting.domain import Channel, GateType
from recruiting.records.memory import CompanyRepository
from recruiting.records.models import Company, ConsentBasis, Contact, ContactRole, OutreachStatus

# Who to write to first, best first.
CONTACT_PREFERENCE = (
    ContactRole.HIRING_MANAGER,
    ContactRole.ENGINEERING_MANAGER,
    ContactRole.DIRECTOR_OF_ENGINEERING,
    ContactRole.TALENT_ACQUISITION,
    ContactRole.HR_MANAGER,
    ContactRole.RECRUITER,
)


class WorkflowStarter(Protocol):
    """What Agent 3 needs from OutreachWorkflow."""

    def start(
        self,
        *,
        gate: GateType,
        subject_id: str,
        channel: Channel,
        recipient: str,
        content: dict[str, str],
        requested_by: str = "agent",
    ) -> str: ...


@dataclass(frozen=True)
class SenderProfile:
    name: str
    postal_address: str


@dataclass
class OutreachDraftReport:
    drafted: list[dict[str, str]] = field(default_factory=list)
    skipped: dict[str, list[str]] = field(default_factory=dict)

    def skip(self, company: Company, reason: str) -> None:
        self.skipped.setdefault(reason, []).append(company.name)


def template_draft(
    company: Company, contact: Contact, roles: list[str], sender: str
) -> ClientEmailDraft:
    """BRD sample email, used when no LLM is configured or the LLM call fails."""
    greeting = f"Dear {contact.full_name.strip()}," if contact.full_name.strip() else "Hello,"
    activity = (
        f"I noticed {company.name} is actively hiring engineering professionals, including "
        f"{', '.join(roles[:3])}."
        if roles
        else f"I noticed {company.name} is actively hiring engineering professionals."
    )
    body = (
        f"{greeting}\n\n{activity}\n\n"
        "CBS LLC specializes in providing highly qualified engineering consultants across "
        "Manufacturing Engineering, Mechanical Design, CAE, Quality, Controls, Electrical, "
        "Automation, Product Development, and Industrial Engineering.\n\n"
        "If you have any open engineering positions, we would appreciate the opportunity to "
        "support your hiring efforts.\n\n"
        "Please share the detailed Job Description(s), and we will promptly identify highly "
        "qualified candidates.\n\nThank you.\n\n"
        f"Regards,\n{sender}\nCBS LLC"
    )
    return ClientEmailDraft(subject="Engineering Talent Support for Your Hiring Needs", body=body)


class ClientOutreachAgent:
    def __init__(
        self,
        companies: CompanyRepository,
        opt_outs: OptOutRegistry,
        workflows: WorkflowStarter,
        sender: SenderProfile,
        unsubscribe: UnsubscribeLinks,
        gateway: LLMGateway | None = None,
        min_priority: int = 70,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self._companies = companies
        self._opt_outs = opt_outs
        self._workflows = workflows
        self._sender = sender
        self._unsubscribe = unsubscribe
        self._gateway = gateway
        self._min_priority = min_priority
        self._on_error = on_error or (lambda _key, _exc: None)

    def contact_problem(self, company: Company, contact: Contact) -> str | None:
        """Why this contact may not receive a first email, or None if they may."""
        if not contact.email:
            return "no_contact_email"
        if not contact.email_valid:  # S6: hard bounces are skipped
            return "no_valid_contact_email"
        if self._opt_outs.is_opted_out(contact.email):
            return "opted_out"
        # CASL: Canadian recipients need a recorded consent basis before any commercial email.
        if (
            contact.country or company.country
        ) == "CA" and contact.consent_basis == ConsentBasis.NONE:
            return "casl_consent_missing"
        return None

    def eligible_contacts(self, company: Company) -> tuple[list[Contact], str | None]:
        """Contacts who may be emailed, best first; or the reason none may."""
        contacts = self._companies.contacts(company.id)
        eligible, last_reason = [], "no_contact_email"
        # Report the reason that removed the last candidates, as choose_contact always has.
        for reason in ("no_contact_email", "no_valid_contact_email", "opted_out",
                       "casl_consent_missing"):  # fmt: skip
            contacts = [c for c in contacts if self.contact_problem(company, c) != reason]
            if not contacts:
                return [], reason
            last_reason = reason
        rank = {role: i for i, role in enumerate(CONTACT_PREFERENCE)}
        eligible = sorted(contacts, key=lambda c: rank.get(c.role, len(rank)))
        return eligible, None if eligible else last_reason

    def choose_contact(self, company: Company) -> tuple[Contact | None, str | None]:
        eligible, reason = self.eligible_contacts(company)
        return (eligible[0], None) if eligible else (None, reason)

    def start_draft(
        self, company: Company, contact: Contact, subject: str, body: str, requested_by: str
    ) -> str:
        """Add the compliance footer and put the email in the BD Manager's approval queue."""
        footer = compliance_footer(
            self._sender.name,
            self._sender.postal_address,
            self._unsubscribe.url(contact.email or ""),
        )
        request_id = self._workflows.start(
            gate=GateType.FIRST_CLIENT_EMAIL,
            subject_id=contact.id,
            channel=Channel.EMAIL,
            recipient=contact.email or "",
            content={"subject": subject.strip(), "body": body.rstrip() + footer},
            requested_by=requested_by,
        )
        # "contacted" = outreach in progress, so the next run does not draft again.
        self._companies.set_outreach_status(company.id, OutreachStatus.CONTACTED)
        return request_id

    def draft(self, company: Company, contact: Contact) -> dict[str, str]:
        roles = sorted(
            {p.target_role for p in self._companies.postings(company.id) if p.target_role}
        )
        draft = None
        if self._gateway is not None:
            try:
                draft = self._gateway.run(
                    "first_client_email",
                    ClientEmailDraft,
                    company=company.name,
                    industry=company.industry or "unknown",
                    contact_name=contact.full_name,
                    contact_title=contact.title or "unknown title",
                    roles=", ".join(roles) or "not known",
                    location=", ".join(company.locations[:2]) or "not known",
                    sender_name=self._sender.name,
                )
            except LLMError as exc:
                self._on_error(company.id, exc)
        if draft is None:
            draft = template_draft(company, contact, roles, self._sender.name)
        footer = compliance_footer(
            self._sender.name,
            self._sender.postal_address,
            self._unsubscribe.url(contact.email or ""),
        )
        return {"subject": draft.subject.strip(), "body": draft.body.rstrip() + footer}

    def run(self, companies: Iterable[Company] | None = None) -> OutreachDraftReport:
        report = OutreachDraftReport()
        if companies is None:
            companies = self._companies.list(min_priority=self._min_priority, limit=10_000)
        for company in companies:
            if company.outreach_status is not OutreachStatus.NOT_CONTACTED:
                report.skip(company, f"status_{company.outreach_status.value}")
                continue
            if company.priority_score < self._min_priority:
                report.skip(company, "low_priority")
                continue
            contact, reason = self.choose_contact(company)
            if contact is None:
                report.skip(company, reason or "no_contact")
                continue
            content = self.draft(company, contact)
            request_id = self._workflows.start(
                gate=GateType.FIRST_CLIENT_EMAIL,
                subject_id=contact.id,
                channel=Channel.EMAIL,
                recipient=contact.email or "",
                content=content,
                requested_by="agent-3",
            )
            # "contacted" = outreach in progress, so the next run does not draft again.
            self._companies.set_outreach_status(company.id, OutreachStatus.CONTACTED)
            report.drafted.append(
                {
                    "company_id": company.id,
                    "contact_id": contact.id,
                    "approval_request_id": request_id,
                }
            )
        return report
