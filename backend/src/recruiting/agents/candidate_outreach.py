"""Agent 8 - LinkedIn and candidate email outreach (BRD Agent 8, plan section 9).

Drafts a LinkedIn connection note from the BRD template and/or a candidate email for ranked
candidates. Each draft starts an outreach workflow that pauses at the recruiter's approval gate
("LinkedIn connection request" or "candidate outreach email", BRD section 10). Approved LinkedIn
notes become tasks the recruiter sends by hand (Q15); approved emails go through the shared
Outreach Service. Opt-outs, duplicate contact and LinkedIn's weekly invitation limit are checked
before anything is drafted.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from recruiting.agents.client_outreach import WorkflowStarter
from recruiting.agents.hiring_signals import target_role
from recruiting.approvals import ApprovalEngine, ApprovalStatus
from recruiting.compliance import OptOutRegistry
from recruiting.compliance.unsubscribe import UnsubscribeLinks, compliance_footer
from recruiting.domain import Channel, GateType
from recruiting.outreach import DeliveryLog, DeliveryStatus
from recruiting.records.engagements import EngagementStore
from recruiting.records.memory import CandidateRepository, new_id
from recruiting.records.models import Candidate, Engagement, EngagementStatus, Job

LINKEDIN_NOTE_LIMIT = 300  # characters LinkedIn allows in a connection note

# Outcomes the recruiter records for a contacted candidate (plan section 9, Tracking).
OUTCOMES = {
    "accepted": EngagementStatus.ACCEPTED,
    "replied": EngagementStatus.REPLIED,
    "interested": EngagementStatus.INTERESTED,
    "not_interested": EngagementStatus.NOT_INTERESTED,
    "do_not_contact": EngagementStatus.DO_NOT_CONTACT,
}
# Engagements in these states block a new outreach draft for the same job.
ACTIVE = {
    EngagementStatus.DRAFTED, EngagementStatus.CONTACTED, EngagementStatus.ACCEPTED,
    EngagementStatus.REPLIED, EngagementStatus.INTERESTED, EngagementStatus.DETAILS_REQUESTED,
    EngagementStatus.COMPLETE, EngagementStatus.NOT_INTERESTED, EngagementStatus.DO_NOT_CONTACT,
}  # fmt: skip


def role_family(job: Job) -> str:
    role = target_role(job.title) or job.title
    return role if role.endswith("s") else f"{role}s"


def first_name(candidate: Candidate) -> str:
    return candidate.full_name.split()[0] if candidate.full_name.strip() else "there"


def linkedin_note(job: Job, candidate: Candidate, recruiter: str) -> str:
    """BRD Agent 8 message, shortened if needed to fit LinkedIn's limit."""
    note = (
        f"Hello {first_name(candidate)},\n\n"
        f"I came across your profile while searching for {role_family(job)}.\n\n"
        "We have exciting engineering opportunities with leading manufacturing companies.\n\n"
        "Would you be interested in discussing them?\n\n"
        f"Regards,\n{recruiter}\nCBS LLC"
    )
    if len(note) > LINKEDIN_NOTE_LIMIT:
        note = (
            f"Hello {first_name(candidate)}, I found your profile while searching for "
            f"{role_family(job)}. We have engineering opportunities with leading manufacturers. "
            f"Interested in discussing them? {recruiter}, CBS LLC"
        )
    return note[:LINKEDIN_NOTE_LIMIT]


def candidate_email(job: Job, candidate: Candidate, recruiter: str, footer: str) -> dict[str, str]:
    """The client is not named before the candidate agrees to be submitted (Phase 6)."""
    skills = [
        s
        for s in job.analysis.get("must_have_skills", [])
        if s.lower() in {x.lower() for x in candidate.skills}
    ]
    where = f" in {job.location}" if job.location else ""
    industry = f"{job.industry.lower()} " if job.industry else ""
    fit = (
        f" Your experience with {', '.join(skills[:3])} looks like a strong match."
        if skills
        else ""
    )
    body = (
        f"Hi {first_name(candidate)},\n\n"
        f"I came across your background while searching for {role_family(job)}. "
        "We are working on a "
        f"{job.title} role with a leading {industry}manufacturer{where}.{fit}\n\n"
        "Would you be open to a short call this week? If you are interested, reply to this email "
        "and I will send the details.\n\n"
        f"Regards,\n{recruiter}\nCBS LLC"
    )
    return {"subject": f"{job.title} opportunity{where}", "body": body + footer}


@dataclass
class OutreachReport:
    drafted: list[dict[str, str]] = field(default_factory=list)
    skipped: dict[str, list[str]] = field(default_factory=dict)

    def skip(self, candidate_id: str, reason: str) -> None:
        self.skipped.setdefault(reason, []).append(candidate_id)


class CandidateOutreachAgent:
    def __init__(
        self,
        candidates: CandidateRepository,
        engagements: EngagementStore,
        approvals: ApprovalEngine,
        deliveries: DeliveryLog,
        opt_outs: OptOutRegistry,
        workflows: WorkflowStarter,
        unsubscribe: UnsubscribeLinks,
        postal_address: str,
        linkedin_weekly_limit: int = 100,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._candidates = candidates
        self._engagements = engagements
        self._approvals = approvals
        self._deliveries = deliveries
        self._opt_outs = opt_outs
        self._workflows = workflows
        self._unsubscribe = unsubscribe
        self._postal_address = postal_address
        self._linkedin_weekly_limit = linkedin_weekly_limit
        self._clock = clock

    def _opted_out(self, candidate: Candidate) -> bool:
        return any(
            self._opt_outs.is_opted_out(a) for a in (candidate.email, candidate.linkedin_url) if a
        )

    def linkedin_used_this_week(self) -> int:
        """Invitations sent or queued in the last 7 days plus drafts waiting for approval."""
        since = self._clock() - timedelta(days=7)
        pending = sum(
            1
            for e in self._engagements.list(status=EngagementStatus.DRAFTED)
            if "linkedin" in e.approvals
        )
        return self._deliveries.sent_count_since(Channel.LINKEDIN, since) + pending

    def draft(
        self, job: Job, candidate_ids: Iterable[str], channels: Iterable[str], recruiter: str
    ) -> OutreachReport:
        report = OutreachReport()
        channels = set(channels)
        now = self._clock()
        for candidate_id in candidate_ids:
            candidate = self._candidates.get(candidate_id)
            if candidate is None:
                report.skip(candidate_id, "unknown_candidate")
                continue
            if self._opted_out(candidate):
                report.skip(candidate_id, "opted_out")
                continue
            engagement = self._engagements.find(job.id, candidate.id)
            if engagement is not None and engagement.status in ACTIVE:
                report.skip(candidate_id, f"already_{engagement.status.value}")
                continue
            engagement = engagement or Engagement(new_id(), job.id, candidate.id)
            engagement.approvals = {}
            started = []

            if "linkedin" in channels:
                if not candidate.linkedin_url:
                    report.skip(candidate_id, "no_linkedin_profile")
                elif self.linkedin_used_this_week() >= self._linkedin_weekly_limit:
                    report.skip(candidate_id, "linkedin_weekly_limit")
                else:
                    approval_id = self._workflows.start(
                        gate=GateType.LINKEDIN_CONNECTION_REQUEST,
                        subject_id=candidate.id,
                        channel=Channel.LINKEDIN,
                        recipient=candidate.linkedin_url,
                        content={"body": linkedin_note(job, candidate, recruiter)},
                        requested_by="agent-8",
                    )
                    engagement.approvals["linkedin"] = approval_id
                    started.append(("linkedin", approval_id))
                    # Save now so the weekly count sees this draft.
                    engagement.status = EngagementStatus.DRAFTED
                    self._engagements.save(engagement)

            if "email" in channels:
                if not candidate.email:
                    report.skip(candidate_id, "no_email")
                else:
                    footer = compliance_footer(
                        recruiter, self._postal_address, self._unsubscribe.url(candidate.email)
                    )
                    approval_id = self._workflows.start(
                        gate=GateType.CANDIDATE_OUTREACH_EMAIL,
                        subject_id=candidate.id,
                        channel=Channel.EMAIL,
                        recipient=candidate.email,
                        content=candidate_email(job, candidate, recruiter, footer),
                        requested_by="agent-8",
                    )
                    engagement.approvals["email"] = approval_id
                    started.append(("email", approval_id))

            if started:
                engagement.status = EngagementStatus.DRAFTED
                engagement.log(now, "drafted", channels=[c for c, _ in started])
                self._engagements.save(engagement)
                for channel, approval_id in started:
                    report.drafted.append(
                        {
                            "candidate_id": candidate.id,
                            "channel": channel,
                            "approval_request_id": approval_id,
                        }
                    )
        return report

    def refresh(self, engagement: Engagement) -> Engagement:
        """Move a drafted engagement on once its approvals are decided and delivered."""
        if engagement.status is not EngagementStatus.DRAFTED or not engagement.approvals:
            return engagement
        statuses = {
            kind: self._approvals.get(aid).status for kind, aid in engagement.approvals.items()
        }
        delivered = [
            kind
            for kind, aid in engagement.approvals.items()
            if (r := self._deliveries.get(aid)) is not None
            and r.status not in (DeliveryStatus.FAILED, DeliveryStatus.RETRY_SCHEDULED)
        ]
        now = self._clock()
        if delivered:
            engagement.status = EngagementStatus.CONTACTED
            engagement.log(now, "contacted", channels=delivered)
        elif all(s is ApprovalStatus.REJECTED for s in statuses.values()):
            engagement.status = EngagementStatus.DRAFT_REJECTED
            engagement.log(now, "draft_rejected")
        else:
            return engagement
        self._engagements.save(engagement)
        return engagement

    def mark_linkedin_sent(self, approval_request_id: str) -> Engagement | None:
        """Q15: the recruiter confirms the approved note was sent in LinkedIn."""
        receipt = self._deliveries.get(approval_request_id)
        if receipt is None or receipt.message.channel is not Channel.LINKEDIN:
            raise KeyError(approval_request_id)
        if receipt.status is DeliveryStatus.QUEUED_FOR_MANUAL_SEND:
            self._deliveries.mark_manually_sent(approval_request_id, self._clock())
        engagement = self._engagements.by_approval(approval_request_id)
        return self.refresh(engagement) if engagement else None

    def record_outcome(self, engagement_id: str, outcome: str, by: str) -> Engagement:
        engagement = self._engagements.get(engagement_id)
        if engagement is None:
            raise KeyError(engagement_id)
        if outcome not in OUTCOMES:
            raise ValueError(f"Unknown outcome {outcome}; use one of {sorted(OUTCOMES)}")
        engagement = self.refresh(engagement)
        engagement.status = OUTCOMES[outcome]
        engagement.log(self._clock(), f"outcome:{outcome}", by=by)
        if outcome == "do_not_contact":
            candidate = self._candidates.get(engagement.candidate_id)
            for address in (candidate.email, candidate.linkedin_url) if candidate else ():
                if address:
                    self._opt_outs.record(address, source=f"recruiter:{by}")
        self._engagements.save(engagement)
        return engagement

    def linkedin_tasks(self) -> list[dict]:
        """Approved notes waiting for the recruiter to send by hand."""
        tasks = []
        for receipt in self._deliveries.receipts():
            if receipt.status is not DeliveryStatus.QUEUED_FOR_MANUAL_SEND:
                continue
            engagement = self._engagements.by_approval(receipt.message.approval_request_id)
            candidate = self._candidates.get(engagement.candidate_id) if engagement else None
            tasks.append(
                {
                    "approval_request_id": receipt.message.approval_request_id,
                    "profile_url": receipt.message.recipient,
                    "note": receipt.message.content.get("body", ""),
                    "approved_at": receipt.delivered_at,
                    "candidate_id": candidate.id if candidate else None,
                    "candidate_name": candidate.full_name if candidate else None,
                    "job_id": engagement.job_id if engagement else None,
                }
            )
        return sorted(tasks, key=lambda t: t["approved_at"])
