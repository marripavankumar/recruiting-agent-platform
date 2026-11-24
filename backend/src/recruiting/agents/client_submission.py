"""Agent 11 - Client Submission, with the recruiter approval step (BRD Agent 11, plan section 10).

1. `prepare`: Agent 10 match report plus a draft package with the nine BRD items (candidate
   summary, resume, skill matrix, match %, availability, rate, visa, location, key projects).
2. The recruiter edits the notes and package, confirms the right to represent the candidate and
   acknowledges any Agent 10 flags; `request_approval` then starts the "resume submission" gate
   (BRD section 10). Before that it checks the candidate's consent to be submitted for this job
   (compliance checklist P2) and blocks a second submission of the same candidate to the same
   client (scenario S18).
3. After approval the Outreach Service emails the hiring manager. The sent version is kept for
   audit, the engagement becomes SUBMITTED and a client follow-up date is set for Agent 12.
   A rejection is kept with its reason for tuning (scenario S19).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from recruiting.agents.client_outreach import WorkflowStarter
from recruiting.agents.matching import ResumeMatchingAgent
from recruiting.approvals import ApprovalEngine, ApprovalStatus, DecisionAction
from recruiting.domain import Channel, GateType
from recruiting.outreach import DeliveryLog, DeliveryStatus
from recruiting.records.engagements import EngagementStore
from recruiting.records.memory import CandidateRepository, CompanyRepository, JobRepository, new_id
from recruiting.records.models import (
    CLOSED_STAGES,
    STAGE_NEXT,
    ClientDecision,
    ConsentStatus,
    ContactRole,
    EngagementStatus,
    JobStatus,
    PlacementStage,
    Submission,
    SubmissionStatus,
)
from recruiting.records.rankings import RankingStore
from recruiting.records.submissions import SubmissionStore

# The nine BRD package items, in the BRD's order.
PACKAGE_ITEMS = (
    "candidate_summary", "resume", "skill_matrix", "match_percent", "availability", "rate",
    "visa", "location", "key_projects",
)  # fmt: skip
EDITABLE = (
    "candidate_summary", "availability", "rate", "visa", "location", "key_projects", "notes",
)  # fmt: skip
# A submission in these states counts as "this candidate is with this client".
LIVE = (SubmissionStatus.PENDING_APPROVAL, SubmissionStatus.SUBMITTED)
INTERVIEW_REACHED = {
    PlacementStage.INTERVIEW_REQUESTED, PlacementStage.INTERVIEW_SCHEDULED,
    PlacementStage.INTERVIEWED, PlacementStage.OFFER_EXTENDED, PlacementStage.OFFER_ACCEPTED,
    PlacementStage.STARTED, PlacementStage.OFFER_DECLINED,
}  # fmt: skip
BLOCKING_FLAGS = ("missing_must_have", "work_authorization", "hard_filter")
CONTACT_ORDER = (
    ContactRole.HIRING_MANAGER, ContactRole.ENGINEERING_MANAGER,
    ContactRole.DIRECTOR_OF_ENGINEERING, ContactRole.TALENT_ACQUISITION, ContactRole.HR_MANAGER,
    ContactRole.RECRUITER, ContactRole.OTHER,
)  # fmt: skip


class SubmissionError(Exception):
    pass


class DuplicateSubmission(SubmissionError):
    pass


class UnknownSubmission(SubmissionError, KeyError):
    pass


@dataclass
class ApprovalInput:
    right_to_represent: bool
    acknowledge_flags: bool = False
    recipient: str | None = None
    other_agency: str | None = None  # another agency is known to have submitted the candidate


def skill_matrix_text(matrix: list[dict[str, Any]]) -> str:
    rows = [
        f"  {'Yes' if m['has'] else 'No ':3}  {m['skill']}"
        f" ({'required' if m['level'] == 'must_have' else 'preferred'})"
        for m in matrix
    ]
    return "\n".join(rows) or "  (no skills listed in the job)"


class ClientSubmissionAgent:
    def __init__(
        self,
        companies: CompanyRepository,
        jobs: JobRepository,
        candidates: CandidateRepository,
        engagements: EngagementStore,
        submissions: SubmissionStore,
        matching: ResumeMatchingAgent,
        approvals: ApprovalEngine | None,
        deliveries: DeliveryLog,
        workflows: WorkflowStarter | None,
        follow_up_days: int = 2,
        rankings: RankingStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        attachment_format: str | None = None,  # "pdf" or "docx": the package goes as a file
        agency: str = "CBS LLC",
    ) -> None:
        self._attachment = attachment_format
        self._agency = agency
        self._companies = companies
        self._jobs = jobs
        self._candidates = candidates
        self._engagements = engagements
        self._submissions = submissions
        self._matching = matching
        self._approvals = approvals
        self._deliveries = deliveries
        self._workflows = workflows
        self._follow_up = timedelta(days=follow_up_days)
        self._rankings = rankings
        self._clock = clock

    # ----- helpers ----------

    def get(self, submission_id: str) -> Submission:
        submission = self._submissions.get(submission_id)
        if submission is None:
            raise UnknownSubmission(submission_id)
        return self.refresh(submission)

    def _live_with_client(self, candidate_id: str, client_id: str, other_than: str | None):
        return [
            s
            for s in self._submissions.list(candidate_id=candidate_id, client_company_id=client_id)
            if s.id != other_than and self.refresh(s).status in LIVE
        ]

    def hiring_manager_email(self, client_company_id: str) -> str | None:
        contacts = [c for c in self._companies.contacts(client_company_id) if c.email]
        contacts.sort(key=lambda c: CONTACT_ORDER.index(c.role))
        return contacts[0].email if contacts else None

    # ----- Agent 10 + draft package ----------

    def prepare(self, job_id: str, candidate_id: str, by: str) -> Submission:
        """Run Agent 10 and draft the package. Re-running refreshes an unsent draft."""
        job = self._jobs.get(job_id)
        candidate = self._candidates.get(candidate_id)
        if job is None or candidate is None:
            raise UnknownSubmission("Unknown job or candidate")
        if job.status is not JobStatus.OPEN:
            raise SubmissionError(f"The job is {job.status}; only open jobs take submissions")
        live = self._live_with_client(candidate_id, job.client_company_id, None)
        if live:
            raise DuplicateSubmission(
                "This candidate is already submitted to this client"
                f" ({'pending approval' if live[0].status is LIVE[0] else 'sent'}"
                f" for job {live[0].job_id})"
            )
        existing = next(
            (
                s
                for s in self._submissions.list(job_id=job_id, candidate_id=candidate_id)
                if s.status in (SubmissionStatus.MATCHED, SubmissionStatus.REJECTED)
            ),
            None,
        )
        now = self._clock()
        report = self._matching.match(job, candidate)
        package = {
            "candidate_name": candidate.full_name,
            "job_code": job.job_code,
            "job_title": job.title,
            "candidate_summary": report["summary"],
            "resume": candidate.resume_text or "",
            "skill_matrix": report["skill_matrix"],
            "match_percent": report["score"],
            "availability": candidate.availability or "",
            "rate": candidate.expected_rate or "",
            "visa": candidate.work_authorization or candidate.visa_status or "",
            "location": candidate.current_location or "",
            "key_projects": report["key_projects"],
            "notes": report["notes"],
        }
        submission = existing or Submission(
            id=new_id(),
            job_id=job.id,
            candidate_id=candidate.id,
            client_company_id=job.client_company_id,
            created_at=now,
        )
        submission.match, submission.package = report, package
        submission.status = SubmissionStatus.MATCHED
        submission.recipient = submission.recipient or self.hiring_manager_email(
            job.client_company_id
        )
        submission.log(now, "matched", by=by, score=report["score"], source=report["source"])
        return self._submissions.save(submission)

    def edit(self, submission_id: str, changes: dict[str, Any], by: str) -> Submission:
        submission = self.get(submission_id)
        if submission.status not in (SubmissionStatus.MATCHED, SubmissionStatus.REJECTED):
            raise SubmissionError(f"A {submission.status} submission cannot be edited")
        unknown = set(changes) - set(EDITABLE)
        if unknown:
            raise SubmissionError(f"Not editable: {', '.join(sorted(unknown))}")
        submission.package.update(changes)
        submission.log(self._clock(), "edited", by=by, fields=sorted(changes))
        return self._submissions.save(submission)

    def withdraw(self, submission_id: str, by: str) -> Submission:
        submission = self.get(submission_id)
        if submission.status not in (SubmissionStatus.MATCHED, SubmissionStatus.REJECTED):
            raise SubmissionError(f"A {submission.status} submission cannot be withdrawn")
        submission.status = SubmissionStatus.WITHDRAWN
        submission.log(self._clock(), "withdrawn", by=by)
        return self._submissions.save(submission)

    # ----- the email ----------

    def cover_email(self, submission: Submission, recruiter: str) -> dict[str, str]:
        p = submission.package
        projects = "\n".join(f"  - {x}" for x in p.get("key_projects") or []) or "  -"
        body = (
            "Hello,\n\n"
            f"Please find below our submission of {p['candidate_name']} for the {p['job_title']}"
            f" role ({p['job_code']}).\n\n"
            f"{p.get('notes') or ''}\n\n"
            f"CANDIDATE SUMMARY\n{p['candidate_summary']}\n\n"
            f"Match: {p['match_percent']}%\n"
            f"Availability: {p.get('availability') or 'to confirm'}\n"
            f"Rate: {p.get('rate') or 'to confirm'}\n"
            f"Visa / work authorisation: {p.get('visa') or 'to confirm'}\n"
            f"Location: {p.get('location') or 'to confirm'}\n\n"
            f"SKILL MATRIX\n{skill_matrix_text(p['skill_matrix'])}\n\n"
            f"KEY PROJECTS\n{projects}\n\n"
            "The candidate has agreed to be submitted for this role. Please reply with your "
            "feedback or preferred interview times.\n\n"
            f"Regards,\n{recruiter}\n{self._agency}"
        )
        if self._attachment:
            kind = "PDF" if self._attachment == "pdf" else "Word document"
            body += f"\n\nThe full submission package, with the resume, is attached as a {kind}."
        else:
            body += f"\n\nRESUME\n{p.get('resume') or '-'}"
        return {
            "subject": f"Candidate submission: {p['job_code']} {p['job_title']} - "
            f"{p['candidate_name']}",
            "body": body,
            # The approver sees, and the content hash covers, the exact package that is sent.
            # The attached document is rendered from this, so it is covered too.
            "package": json.dumps(
                {
                    k: p.get(k)
                    for k in ("candidate_name", "job_code", "job_title", *PACKAGE_ITEMS, "notes")
                },
                ensure_ascii=False,
            ),  # fmt: skip
            "warnings": "; ".join(submission.warnings),
        }

    # ----- recruiter approval step ----------

    def request_approval(self, submission_id: str, data: ApprovalInput, by: str, recruiter: str):
        if self._workflows is None:
            raise SubmissionError("Submission workflows are not running")
        submission = self.get(submission_id)
        if submission.status not in (SubmissionStatus.MATCHED, SubmissionStatus.REJECTED):
            raise SubmissionError(f"A {submission.status} submission cannot be sent for approval")
        if not data.right_to_represent:
            raise SubmissionError(
                "Confirm that the candidate agreed to be represented by CBS LLC for this job"
            )
        flags = [f for f in submission.match.get("flags", []) if f["code"] in BLOCKING_FLAGS]
        if flags and not data.acknowledge_flags:
            raise SubmissionError(
                "Agent 10 flagged: "
                + "; ".join(f["message"] for f in flags)
                + ". Acknowledge the flags to submit anyway."
            )
        candidate = self._candidates.get(submission.candidate_id)
        engagement = self._engagements.find(submission.job_id, submission.candidate_id)
        if (
            candidate is None
            or candidate.consent_status is not ConsentStatus.GRANTED
            or engagement is None
            or engagement.consent_submit_at is None
        ):
            raise SubmissionError(
                "No recorded consent from the candidate to be submitted for this job (P2)"
            )
        if engagement.missing or engagement.status not in (
            EngagementStatus.COMPLETE, EngagementStatus.SUBMITTED,
        ):  # fmt: skip
            # Scenario S16: Agent 9 must have everything before a submission.
            missing = ", ".join(engagement.missing) or str(engagement.status)
            raise SubmissionError(f"The candidate's details are not complete ({missing})")
        if not (submission.package.get("resume") or "").strip():
            raise SubmissionError("The package has no resume")
        live = self._live_with_client(
            submission.candidate_id, submission.client_company_id, submission.id
        )
        if live:
            raise DuplicateSubmission("This candidate is already submitted to this client")
        recipient = data.recipient or submission.recipient
        if not recipient:
            raise SubmissionError("No hiring manager email on file for this client")

        now = self._clock()
        submission.recipient = recipient
        submission.right_to_represent_at, submission.right_to_represent_by = now, by
        submission.warnings = [w for w in submission.warnings if not w.startswith("Another")]
        if data.other_agency:
            submission.warnings.append(
                f"Another agency is known to have submitted this candidate: {data.other_agency}"
            )
        if flags:
            submission.warnings += [f"Acknowledged: {f['message']}" for f in flags]
        submission.warnings = list(dict.fromkeys(submission.warnings))
        submission.rejection_reason = None
        submission.approval_request_id = self._workflows.start(
            gate=GateType.RESUME_SUBMISSION,
            subject_id=submission.id,
            channel=Channel.EMAIL,
            recipient=recipient,
            content=self.cover_email(submission, recruiter),
            requested_by="agent-11",  # the recruiter then approves it at the gate
        )
        submission.status = SubmissionStatus.PENDING_APPROVAL
        submission.log(now, "approval_requested", by=by, approval=submission.approval_request_id)
        return self._submissions.save(submission)

    # ----- after the gate ----------

    def refresh(self, submission: Submission) -> Submission:
        """Move a pending submission on once its approval is decided and delivered."""
        if submission.status is not SubmissionStatus.PENDING_APPROVAL or self._approvals is None:
            return submission
        aid = submission.approval_request_id
        request = self._approvals.get(aid)
        receipt = self._deliveries.get(aid)
        now = self._clock()
        if request.status is ApprovalStatus.REJECTED:
            rejections = [
                d
                for d in request.decisions
                if d.action in (DecisionAction.REJECT, DecisionAction.CANCEL)
            ]
            submission.status = SubmissionStatus.REJECTED
            submission.rejection_reason = rejections[-1].reason if rejections else None
            submission.log(now, "rejected", reason=submission.rejection_reason)
        elif receipt is not None and receipt.status is DeliveryStatus.SENT:
            submission.status = SubmissionStatus.SUBMITTED
            submission.sent_at = receipt.delivered_at
            submission.sent_content = dict(receipt.message.content)
            submission.recipient = receipt.message.recipient
            submission.follow_up_at = receipt.delivered_at + self._follow_up
            submission.stage, submission.stage_at = PlacementStage.SUBMITTED, receipt.delivered_at
            submission.log(now, "submitted", to=receipt.message.recipient)
            engagement = self._engagements.find(submission.job_id, submission.candidate_id)
            if engagement is not None:
                engagement.status = EngagementStatus.SUBMITTED
                engagement.log(now, "submitted", submission=submission.id)
                self._engagements.save(engagement)
        elif receipt is not None and receipt.status is DeliveryStatus.FAILED:
            submission.status = SubmissionStatus.FAILED
            submission.log(now, "delivery_failed", error=receipt.error)
        else:
            return submission
        return self._submissions.save(submission)

    def list(self, **filters: Any) -> list[Submission]:
        return [self.refresh(s) for s in self._submissions.list(**filters)]

    # ----- client portal (Q8) ----------

    def record_client_feedback(
        self, submission_id: str, client_company_id: str, decision: ClientDecision,
        comment: str | None, by: str,
    ) -> Submission:  # fmt: skip
        submission = self.get(submission_id)
        if (
            submission.client_company_id != client_company_id
            or submission.status is not SubmissionStatus.SUBMITTED
        ):
            raise UnknownSubmission(submission_id)
        now = self._clock()
        submission.client_decision = decision
        submission.client_feedback = (comment or "").strip()[:2000] or None
        submission.follow_up_at = (
            None if decision is not ClientDecision.HOLD else now + self._follow_up
        )
        submission.log(now, "client_feedback", by=by, decision=decision.value)
        stage = submission.stage or PlacementStage.SUBMITTED
        if decision is ClientDecision.INTERVIEW and stage is PlacementStage.SUBMITTED:
            self._move(submission, PlacementStage.INTERVIEW_REQUESTED, now, by)
        elif decision is ClientDecision.REJECTED and stage not in CLOSED_STAGES:
            # Scenario S20: status Rejected with the client's reason.
            submission.stage_reason = submission.client_feedback or "Rejected by the client"
            self._move(submission, PlacementStage.CLIENT_REJECTED, now, by)
        elif decision is ClientDecision.HOLD:
            submission.stage_at = now  # the feedback follow-up counts again from today
        return self._submissions.save(submission)

    # ----- pipeline (plan section 11) ----------

    def _move(self, submission: Submission, stage: PlacementStage, now: datetime, by: str):
        before = submission.stage
        submission.stage, submission.stage_at = stage, now
        submission.log(now, "stage", by=by, stage=stage.value, previous=before and before.value)

    def set_stage(
        self, submission_id: str, stage: PlacementStage, by: str, *,
        interview_at: datetime | None = None, offer_at: datetime | None = None,
        start_date: date | None = None, reason: str | None = None,
    ) -> tuple[Submission, list[dict[str, Any]]]:  # fmt: skip
        """Record Interview, Offer, Start or a rejection / withdrawal. A withdrawal returns the
        next-ranked candidates not yet submitted for the job (scenario S21)."""
        submission = self.get(submission_id)
        current = submission.stage
        if submission.status is not SubmissionStatus.SUBMITTED or current is None:
            raise SubmissionError("Only a sent submission has a pipeline stage")
        if current in CLOSED_STAGES:
            raise SubmissionError(f"The submission is already {current}")
        allowed = STAGE_NEXT[current] | {PlacementStage.CANDIDATE_WITHDRAWN}
        if stage not in allowed:
            raise SubmissionError(
                f"Cannot go from {current} to {stage}; next: {', '.join(sorted(allowed))}"
            )
        reason = (reason or "").strip()[:1000] or None
        if stage is PlacementStage.INTERVIEW_SCHEDULED and interview_at is None:
            raise SubmissionError("Give the interview date and time")
        if stage is PlacementStage.OFFER_ACCEPTED and start_date is None:
            raise SubmissionError("Give the start date")
        if stage in (
            PlacementStage.CLIENT_REJECTED, PlacementStage.OFFER_DECLINED,
            PlacementStage.CANDIDATE_WITHDRAWN,
        ) and not reason:  # fmt: skip
            raise SubmissionError("Give a reason")
        now = self._clock()
        if interview_at is not None:
            submission.interview_at = interview_at
        if stage is PlacementStage.OFFER_EXTENDED:
            submission.offer_at = offer_at or now
        if start_date is not None:
            submission.start_date = start_date
        submission.stage_reason = reason or submission.stage_reason
        self._move(submission, stage, now, by)
        self._submissions.save(submission)
        suggestions = (
            self.next_candidates(submission.job_id)
            if stage is PlacementStage.CANDIDATE_WITHDRAWN
            else []
        )
        return submission, suggestions

    def next_candidates(self, job_id: str, limit: int = 3) -> list[dict[str, Any]]:
        ranking = self._rankings.get(job_id) if self._rankings else None
        if ranking is None:
            return []
        taken = {s.candidate_id for s in self._submissions.list(job_id=job_id)}
        out = []
        for entry in ranking.top:
            if entry.candidate_id in taken:
                continue
            candidate = self._candidates.get(entry.candidate_id)
            out.append({"candidate_id": entry.candidate_id, "rank": entry.rank,
                        "score": entry.score,
                        "name": candidate.full_name if candidate else None})  # fmt: skip
            if len(out) == limit:
                break
        return out

    # ----- reporting ----------

    def summary(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        items = self.list(job_id=job_id)
        counts = {s.value: 0 for s in SubmissionStatus}
        for s in items:
            counts[s.status.value] += 1
        sent = [s for s in items if s.status is SubmissionStatus.SUBMITTED]
        first = min((s.sent_at for s in sent if s.sent_at), default=None)
        hours = (
            round((first - job.created_at).total_seconds() / 3600, 1)
            if job and job.created_at and first
            else None
        )
        compliant = sum(
            1 for s in sent if s.approval_request_id and s.right_to_represent_at and s.sent_content
        )
        interviews = sum(1 for s in sent if s.stage in INTERVIEW_REACHED)
        return {
            **counts,
            "total": len(items),
            # Phase 6 exit criteria: JD to first submission < 24 h; 100% approved with consent.
            "hours_to_first_submission": hours,
            "approved_with_consent_rate": round(compliant / len(sent), 3) if sent else None,
            # KPI: resume-to-interview ratio > 25%.
            "interview_rate": round(interviews / len(sent), 3) if sent else None,
        }
