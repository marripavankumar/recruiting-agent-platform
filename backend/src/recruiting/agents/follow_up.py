"""Agent 12 - Follow-up (BRD Agent 12, plan section 11).

Each run reads the Company, Job and Candidate records and keeps one follow-up item per open
step: a client or candidate who has not replied, missing candidate details, a submission or an
interview with no feedback, an interview to schedule or prepare, an offer to track, a start
date to check. Due dates follow the Administrator's cadence (Q13; default 3, 7 and 14 days, then
stop). Items close by themselves when the record moves on, and are cancelled when the job is
closed or the person opts out.

When a message follow-up falls due, Agent 12 drafts it into the same approval gate as the first
contact (BRD section 10); nothing is sent without approval. Other items are tasks in the owner's
list. Overdue open items are the alerts that keep a record from stalling unseen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

from recruiting.agents.candidate_outreach import first_name, role_family
from recruiting.agents.candidate_search import shortfall
from recruiting.agents.client_outreach import WorkflowStarter
from recruiting.agents.client_submission import ClientSubmissionAgent
from recruiting.agents.resume_collection import CollectionError, ResumeCollectionAgent
from recruiting.approvals import ApprovalEngine, ApprovalStatus, Reviewer
from recruiting.compliance import OptOutRegistry
from recruiting.compliance.unsubscribe import UnsubscribeLinks, compliance_footer
from recruiting.domain import Channel, GateType
from recruiting.outreach import DeliveryLog, DeliveryStatus
from recruiting.records.engagements import EngagementStore
from recruiting.records.followups import CadenceStore, FollowUpStore
from recruiting.records.memory import CandidateRepository, CompanyRepository, JobRepository, new_id
from recruiting.records.models import (
    MESSAGE_KINDS,
    EngagementStatus,
    FollowUp,
    FollowUpKind,
    FollowUpStatus,
    Job,
    JobStatus,
    OutreachStatus,
    PlacementStage,
    Submission,
    SubmissionStatus,
)
from recruiting.records.rankings import RankingStore

K = FollowUpKind
OWNERS: dict[FollowUpKind, str] = {
    K.CLIENT_OUTREACH: "bd_manager",
    K.SUBMISSION_FEEDBACK: "account_manager",
    K.CANDIDATE_OUTREACH: "recruiter",
    K.CANDIDATE_DETAILS: "recruiter",
    K.INTERVIEW_SCHEDULING: "recruiter",
    K.INTERVIEW_PREP: "recruiter",
    K.INTERVIEW_FEEDBACK: "account_manager",
    K.OFFER: "account_manager,recruiter",
    K.START_DATE: "recruiter",
    K.CANDIDATE_SHORTFALL: "recruiter",
}
NOTES: dict[FollowUpKind, str] = {
    K.CLIENT_OUTREACH: "No reply to the first client email",
    K.SUBMISSION_FEEDBACK: "Submission sent; no client feedback yet",
    K.CANDIDATE_OUTREACH: "Candidate contacted; no reply yet",
    K.CANDIDATE_DETAILS: "Candidate still owes details",
    K.INTERVIEW_SCHEDULING: "Client asked for an interview: agree a time",
    K.INTERVIEW_PREP: "Confirm the interview time and prepare the candidate",
    K.INTERVIEW_FEEDBACK: "Interview held: collect the client's feedback and outcome",
    K.OFFER: "Offer extended: track the candidate's decision and the terms",
    K.START_DATE: "Offer accepted: confirm documents and check in around the start date",
    K.CANDIDATE_SHORTFALL: "Too few qualified candidates: review and widen the search",
}
GATES: dict[FollowUpKind, GateType] = {
    K.CLIENT_OUTREACH: GateType.FIRST_CLIENT_EMAIL,
    # Messages about a submitted candidate go through the resume-submission gate.
    K.SUBMISSION_FEEDBACK: GateType.RESUME_SUBMISSION,
    K.INTERVIEW_FEEDBACK: GateType.RESUME_SUBMISSION,
    K.CANDIDATE_OUTREACH: GateType.CANDIDATE_OUTREACH_EMAIL,
}


@dataclass
class Desired:
    kind: FollowUpKind
    subject_id: str
    anchor_at: datetime
    job_id: str | None = None
    candidate_id: str | None = None
    company_id: str | None = None
    owner_id: str | None = None
    note: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunReport:
    created: int = 0
    closed: int = 0
    cancelled: int = 0
    drafted: list[dict[str, str]] = field(default_factory=list)
    alerts: int = 0


class FollowUpError(Exception):
    pass


class FollowUpAgent:
    def __init__(
        self,
        items: FollowUpStore,
        cadence: CadenceStore,
        companies: CompanyRepository,
        jobs: JobRepository,
        candidates: CandidateRepository,
        engagements: EngagementStore,
        submissions: ClientSubmissionAgent,
        collection: ResumeCollectionAgent,
        approvals: ApprovalEngine | None,
        deliveries: DeliveryLog,
        workflows: WorkflowStarter | None,
        opt_outs: OptOutRegistry,
        unsubscribe: UnsubscribeLinks,
        sender_name: str,
        postal_address: str,
        user_name: Callable[[str | None], str | None] = lambda _id: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rankings: RankingStore | None = None,
    ) -> None:
        self._rankings = rankings
        self._items = items
        self._cadence = cadence
        self._companies = companies
        self._jobs = jobs
        self._candidates = candidates
        self._engagements = engagements
        self._submissions = submissions
        self._collection = collection
        self._approvals = approvals
        self._deliveries = deliveries
        self._workflows = workflows
        self._opt_outs = opt_outs
        self._unsubscribe = unsubscribe
        self._sender = sender_name
        self._postal = postal_address
        self._user_name = user_name
        self._clock = clock

    # ----- what should be followed up ----------

    def _open_job(self, job_id: str | None) -> Job | None:
        job = self._jobs.get(job_id) if job_id else None
        return job if job is not None and job.status is JobStatus.OPEN else None

    def desired(self) -> list[Desired]:
        out: list[Desired] = []
        out += self._client_outreach()
        out += self._candidates_due()
        out += self._pipeline()
        out += self._shortfalls()
        return out

    def _shortfalls(self) -> list[Desired]:
        """S12: open jobs whose ranking has fewer than 10 qualified candidates."""
        if self._rankings is None:
            return []
        out = []
        for job in self._jobs.list(status=JobStatus.OPEN):
            ranking = self._rankings.get(job.id)
            gap = shortfall(job, ranking) if ranking else None
            if gap is None or ranking is None:
                continue
            out.append(Desired(
                K.CANDIDATE_SHORTFALL, job.id, ranking.ranked_at, job_id=job.id,
                owner_id=job.recruiter_id,
                note=f"Only {gap['qualified']} of {gap['target']} qualified candidates for "
                f"{job.job_code}: review the ranking and widen the search",
                context={"qualified": gap["qualified"], "widened": gap["widened"]},
            ))  # fmt: skip
        return out

    def _client_outreach(self) -> list[Desired]:
        """First client emails (Agent 3) that were sent and got no reply (scenario S8)."""
        out: dict[str, Desired] = {}
        if self._approvals is None:
            return []
        for receipt in self._deliveries.receipts():
            message = receipt.message
            if receipt.status is not DeliveryStatus.SENT or receipt.replied_at is not None:
                continue
            request = self._approvals.get(message.approval_request_id)
            if request.gate is not GateType.FIRST_CLIENT_EMAIL or request.requested_by != "agent-3":
                continue
            contact = self._companies.contact_by_email(message.recipient)
            company = self._companies.get(contact.company_id) if contact else None
            if company is None or company.outreach_status is not OutreachStatus.CONTACTED:
                continue
            if company.id in out and out[company.id].anchor_at <= receipt.delivered_at:
                continue
            out[company.id] = Desired(
                K.CLIENT_OUTREACH, company.id, receipt.delivered_at, company_id=company.id,
                note=f"{NOTES[K.CLIENT_OUTREACH]} ({company.name})",
                context={"recipient": message.recipient, "subject": message.content.get("subject"),
                         "name": contact.full_name if contact else "", "company": company.name},
            )  # fmt: skip
        return list(out.values())

    def _candidates_due(self) -> list[Desired]:
        out = []
        for e in self._engagements.list():
            job = self._open_job(e.job_id)
            candidate = self._candidates.get(e.candidate_id)
            if job is None or candidate is None:
                continue
            base = dict(job_id=job.id, candidate_id=candidate.id, owner_id=job.recruiter_id)
            if e.status in (EngagementStatus.CONTACTED, EngagementStatus.ACCEPTED):
                events = [h for h in e.history if h["event"] in ("contacted", "outcome:accepted")]
                anchor = datetime.fromisoformat(events[-1]["at"]) if events else e.updated_at
                if anchor is None:
                    continue
                out.append(Desired(
                    K.CANDIDATE_OUTREACH, e.id, anchor, **base,
                    note=f"{NOTES[K.CANDIDATE_OUTREACH]} ({candidate.full_name}, {job.job_code})"
                    + ("" if candidate.email else ": follow up in LinkedIn by hand (Q15)"),
                    context={"recipient": candidate.email, "name": candidate.full_name},
                ))  # fmt: skip
            elif e.status is EngagementStatus.DETAILS_REQUESTED and e.requested_at:
                # Count from the first request: each reminder re-issues the link.
                asked = [h["at"] for h in e.history if h["event"] == "details_requested"]
                anchor = datetime.fromisoformat(asked[0]) if asked else e.requested_at
                missing = ", ".join(e.missing) or "details"
                out.append(Desired(
                    K.CANDIDATE_DETAILS, e.id, anchor, **base,
                    note=f"{candidate.full_name} still owes: {missing} ({job.job_code})",
                    context={"recipient": candidate.email, "name": candidate.full_name},
                ))  # fmt: skip
        return out

    @staticmethod
    def _pipeline_step(s: Submission, now: datetime) -> tuple[FollowUpKind, datetime, bool] | None:
        """(kind, anchor, owned by the job's recruiter) for a sent submission's stage."""
        stage = s.stage
        if stage is PlacementStage.SUBMITTED and s.stage_at:
            return K.SUBMISSION_FEEDBACK, s.stage_at, False
        if stage is PlacementStage.INTERVIEW_REQUESTED and s.stage_at:
            return K.INTERVIEW_SCHEDULING, s.stage_at, True
        if stage is PlacementStage.INTERVIEW_SCHEDULED and s.interview_at:
            if s.interview_at > now:
                return K.INTERVIEW_PREP, s.interview_at, True
            return K.INTERVIEW_FEEDBACK, s.interview_at, False
        anchor = s.interview_at or s.stage_at
        if stage is PlacementStage.INTERVIEWED and anchor:
            return K.INTERVIEW_FEEDBACK, anchor, False
        anchor = s.offer_at or s.stage_at
        if stage is PlacementStage.OFFER_EXTENDED and anchor:
            return K.OFFER, anchor, False
        if stage in (PlacementStage.OFFER_ACCEPTED, PlacementStage.STARTED) and s.start_date:
            return K.START_DATE, datetime.combine(s.start_date, time(9), tzinfo=UTC), True
        return None

    def _pipeline(self) -> list[Desired]:
        out = []
        now = self._clock()
        for s in self._submissions.list(status=SubmissionStatus.SUBMITTED):
            job = self._open_job(s.job_id)
            step = self._pipeline_step(s, now) if job is not None else None
            if job is None or step is None:
                continue
            kind, anchor, recruiter_owned = step
            name = s.package.get("candidate_name", "")
            context = {"recipient": s.recipient, "subject": s.sent_content.get("subject"),
                       "name": name, "job": f"{job.title} ({job.job_code})"}  # fmt: skip
            if s.interview_at:
                context["interview_at"] = s.interview_at.isoformat()
            out.append(Desired(
                kind, s.id, anchor, job_id=job.id, candidate_id=s.candidate_id,
                company_id=s.client_company_id,
                owner_id=job.recruiter_id if recruiter_owned else None,
                note=f"{NOTES[kind]} ({name}, {job.job_code})", context=context,
            ))  # fmt: skip
        return out

    # ----- the sweep ----------

    def _due(self, item: FollowUp, days: list[int]) -> datetime | None:
        return (
            item.anchor_at + timedelta(days=days[item.attempt])
            if item.attempt < len(days)
            else None
        )

    def run(self, auto_draft: bool = True) -> RunReport:
        now = self._clock()
        cadence = self._cadence.get()
        report = RunReport()
        wanted = {(d.kind, d.subject_id): d for d in self.desired()}

        for (kind, subject_id), d in wanted.items():
            item = self._items.find(kind, subject_id)
            if item is None:
                item = FollowUp(
                    new_id(), kind, subject_id, OWNERS[kind], d.anchor_at, None,
                    job_id=d.job_id, candidate_id=d.candidate_id, company_id=d.company_id,
                    owner_id=d.owner_id, note=d.note, context=d.context, created_at=now,
                )  # fmt: skip
                item.due_at = self._due(item, cadence[kind])
                item.log(now, "created", due=item.due_at and item.due_at.isoformat())
                report.created += 1
            elif item.status is not FollowUpStatus.OPEN:
                if d.anchor_at <= item.anchor_at:
                    continue  # finished for this event; a newer event reopens it
                item.status, item.attempt, item.anchor_at = FollowUpStatus.OPEN, 0, d.anchor_at
                item.due_at = self._due(item, cadence[kind])
                item.log(now, "reopened")
            else:
                if d.anchor_at != item.anchor_at:  # e.g. the interview was moved
                    item.anchor_at = d.anchor_at
                    item.due_at = self._due(item, cadence[kind])
                    item.log(now, "rescheduled")
                item.note, item.context, item.owner_id = d.note, d.context, d.owner_id
            self._items.save(item)

        for item in self._items.list(status=FollowUpStatus.OPEN):
            if (item.kind, item.subject_id) in wanted:
                continue
            closed_job = item.job_id is not None and self._open_job(item.job_id) is None
            self._close(item, FollowUpStatus.CANCELLED if closed_job else FollowUpStatus.DONE,
                        "job_closed" if closed_job else "resolved", now)  # fmt: skip
            report.cancelled += closed_job
            report.closed += not closed_job

        for item in self._items.list(status=FollowUpStatus.OPEN):
            if not (item.due_at and item.due_at <= now):
                continue
            if auto_draft and item.kind in MESSAGE_KINDS and item.context.get("recipient"):
                drafted = self._draft(item, cadence[item.kind], now)
                if drafted:
                    report.drafted.append(drafted)
                    continue
            report.alerts += 1
        return report

    def _close(self, item: FollowUp, status: FollowUpStatus, why: str, now: datetime) -> None:
        item.status, item.due_at = status, None
        item.log(now, status.value, reason=why)
        self._items.save(item)

    # ----- drafting a follow-up message ----------

    def _last_pending(self, item: FollowUp) -> bool:
        if not item.approvals or self._approvals is None:
            return False
        return self._approvals.get(item.approvals[-1]).status is ApprovalStatus.PENDING

    def _advance(self, item: FollowUp, days: list[int], now: datetime, event: str, **kw) -> None:
        item.attempt += 1
        item.due_at = self._due(item, days)
        item.log(now, event, attempt=item.attempt, **kw)
        if item.due_at is None:
            last = FollowUpStatus.STOPPED if item.kind in MESSAGE_KINDS else FollowUpStatus.DONE
            item.status = last
            item.log(now, last.value, reason="cadence_complete")

    def _draft(self, item: FollowUp, days: list[int], now: datetime) -> dict[str, str] | None:
        """Draft the next follow-up message into its approval gate. None if nothing drafted."""
        if self._workflows is None or self._last_pending(item):
            return None
        recipient = item.context["recipient"]
        if self._opt_outs.is_opted_out(recipient):
            self._close(item, FollowUpStatus.CANCELLED, "opted_out", now)
            return None
        if item.kind is K.CANDIDATE_DETAILS:
            # Agent 9 drafts the reminder with a fresh secure link (same approval gate).
            try:
                engagement, _ = self._collection.request(item.subject_id, self._signature(item))
            except CollectionError as exc:
                self._close(item, FollowUpStatus.CANCELLED, str(exc), now)
                return None
            approval_id = engagement.approvals.get("details_request")
        else:
            approval_id = self._workflows.start(
                gate=GATES[item.kind],
                subject_id=item.subject_id,
                channel=Channel.EMAIL,
                recipient=recipient,
                content=self.message(item, item.attempt + 1, len(days)),
                requested_by="agent-12",
            )
        if approval_id is None:
            return None
        item.approvals.append(approval_id)
        self._advance(item, days, now, "drafted", approval=approval_id)
        self._items.save(item)
        return {
            "follow_up_id": item.id,
            "kind": item.kind.value,
            "approval_request_id": approval_id,
        }

    def _signature(self, item: FollowUp) -> str:
        if item.kind is K.CLIENT_OUTREACH:
            return self._sender
        return self._user_name(item.owner_id) or "CBS recruiting team"

    def message(self, item: FollowUp, attempt: int, total: int) -> dict[str, str]:
        c = item.context
        sign = self._signature(item)
        subject = c.get("subject") or "Following up"
        subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        last = attempt >= total
        if item.kind is K.CLIENT_OUTREACH:
            surname = (c.get("name") or "").split()[-1:] or [""]
            greeting = f"Dear {c.get('name')}," if surname[0] else "Hello,"
            middle = (
                "This is my last note on this for now. If engineering hiring comes up later, "
                "we would be glad to help."
                if last
                else "I wanted to follow up on my earlier note. If you have open engineering "
                "positions, please share the job descriptions and we will promptly identify "
                "qualified candidates."
            )
            footer = compliance_footer(sign, self._postal, self._unsubscribe.url(c["recipient"]))
            return {"subject": subject, "body": f"{greeting}\n\n{middle}\n\nRegards,\n{sign}\n"
                    f"CBS LLC{footer}"}  # fmt: skip
        if item.kind is K.CANDIDATE_OUTREACH:
            job = self._jobs.get(item.job_id or "")
            candidate = self._candidates.get(item.candidate_id or "")
            roles = role_family(job) if job else "engineering roles"
            hello = first_name(candidate) if candidate else "there"
            middle = (
                "I will not follow up again, but my door is open if you would like to talk."
                if last
                else f"Just following up on my earlier note about {roles} opportunities. Would "
                "you be open to a quick conversation?"
            )
            footer = compliance_footer(sign, self._postal, self._unsubscribe.url(c["recipient"]))
            body = f"Hi {hello},\n\n{middle}\n\nRegards,\n{sign}\nCBS LLC{footer}"
            return {"subject": "Following up: engineering opportunities", "body": body}
        if item.kind is K.SUBMISSION_FEEDBACK:
            middle = (
                f"Following up on our submission of {c.get('name')} for the {c.get('job')} role. "
                "Could you share your feedback, or a few times for an interview?"
            )
        else:  # interview feedback
            middle = (
                f"Thank you for interviewing {c.get('name')} for the {c.get('job')} role. Could "
                "you share your feedback and the next step?"
            )
        return {"subject": subject, "body": f"Hello,\n\n{middle}\n\nRegards,\n{sign}\nCBS LLC"}

    # ----- user actions ----------

    def get(self, follow_up_id: str) -> FollowUp:
        item = self._items.get(follow_up_id)
        if item is None:
            raise KeyError(follow_up_id)
        return item

    def _open(self, follow_up_id: str) -> FollowUp:
        item = self.get(follow_up_id)
        if item.status is not FollowUpStatus.OPEN:
            raise FollowUpError(f"The follow-up is {item.status}")
        return item

    def complete(self, follow_up_id: str, by: str, note: str | None = None) -> FollowUp:
        """The owner did this step (a call, a LinkedIn message, the interview prep ...)."""
        item = self._open(follow_up_id)
        days = self._cadence.get()[item.kind]
        self._advance(item, days, self._clock(), "completed", by=by, note=(note or "")[:500])
        return self._items.save(item)

    def snooze(self, follow_up_id: str, until: datetime, by: str) -> FollowUp:
        item = self._open(follow_up_id)
        now = self._clock()
        if until <= now or until > now + timedelta(days=60):
            raise FollowUpError("Snooze to a time within the next 60 days")
        item.due_at = until
        item.log(now, "snoozed", by=by, until=until.isoformat())
        return self._items.save(item)

    def draft_now(self, follow_up_id: str, by: str) -> dict[str, str]:
        item = self._open(follow_up_id)
        if item.kind not in MESSAGE_KINDS or not item.context.get("recipient"):
            raise FollowUpError("This follow-up is a task, not a message")
        if self._last_pending(item):
            raise FollowUpError("The previous follow-up is still waiting for approval")
        drafted = self._draft(item, self._cadence.get()[item.kind], self._clock())
        if drafted is None:
            raise FollowUpError("Nothing was drafted (opted out, or workflows are not running)")
        return drafted

    def for_user(self, role: str, user_id: str | None) -> list[FollowUp]:
        return [
            i
            for i in self._items.list(status=FollowUpStatus.OPEN)
            if role == "administrator"
            or (role in i.owner_role.split(",") and (i.owner_id in (None, user_id)))
        ]

    # ----- job closed (scenario S22) ----------

    def close_job(self, job_id: str, by: Reviewer, reason: str) -> dict[str, int]:
        """Stop everything still in flight for a filled or cancelled job."""
        now = self._clock()
        to_cancel: list[str] = []
        for e in self._engagements.list(job_id=job_id):
            to_cancel += list(e.approvals.values())
        for s in self._submissions.list(job_id=job_id):
            if s.status is SubmissionStatus.PENDING_APPROVAL and s.approval_request_id:
                to_cancel.append(s.approval_request_id)
        items = [i for i in self._items.list(job_id=job_id) if i.status is FollowUpStatus.OPEN]
        for item in items:
            to_cancel += item.approvals
            self._close(item, FollowUpStatus.CANCELLED, "job_closed", now)
        cancelled = 0
        if self._approvals is not None:
            for approval_id in dict.fromkeys(to_cancel):
                if self._approvals.get(approval_id).status is ApprovalStatus.PENDING:
                    self._approvals.cancel(approval_id, by, reason)
                    cancelled += 1
                    resume = getattr(self._workflows, "resume", None)
                    if resume is not None:
                        resume(approval_id)  # ends the paused workflow without sending
        self._submissions.list(job_id=job_id)  # refresh: pending submissions become rejected
        return {"approvals_cancelled": cancelled, "follow_ups_cancelled": len(items)}

    # ----- reporting ----------

    def summary(self) -> dict[str, Any]:
        now = self._clock()
        open_items = self._items.list(status=FollowUpStatus.OPEN)
        by_kind: dict[str, int] = {}
        for i in open_items:
            by_kind[i.kind.value] = by_kind.get(i.kind.value, 0) + 1
        return {
            "open": len(open_items),
            # Phase 7 exit criteria: every open item has a date; nothing overdue goes unseen.
            "open_without_due_date": sum(1 for i in open_items if i.due_at is None),
            "overdue": sum(1 for i in open_items if i.due_at and i.due_at <= now),
            "stopped": len(self._items.list(status=FollowUpStatus.STOPPED)),
            "by_kind": by_kind,
        }
