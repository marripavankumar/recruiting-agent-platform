"""Matching and client submission API (Agents 10-11, plan section 10) and the client portal view.

Staff endpoints need the Recruiter, Account Manager or Administrator role; only a Recruiter or
Administrator sends a package for approval. Client users (Q8) see only submissions that were
sent to their own company, exactly as sent, and record their decision.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from recruiting.agents.client_submission import (
    ApprovalInput,
    ClientSubmissionAgent,
    SubmissionError,
    UnknownSubmission,
)
from recruiting.api.deps import ServicesDep, UserDep, require_platform, require_role
from recruiting.domain import Role
from recruiting.records.models import ClientDecision, PlacementStage, Submission

router = APIRouter(tags=["submissions"])

TALENT = (Role.ADMINISTRATOR, Role.RECRUITER, Role.ACCOUNT_MANAGER)
SENDERS = (Role.ADMINISTRATOR, Role.RECRUITER)


class PrepareIn(BaseModel):
    candidate_id: str


class EditIn(BaseModel):
    candidate_summary: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)
    availability: str | None = Field(default=None, max_length=300)
    rate: str | None = Field(default=None, max_length=300)
    visa: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    key_projects: list[str] | None = Field(default=None, max_length=5)


class ApprovalIn(BaseModel):
    right_to_represent: bool
    acknowledge_flags: bool = False
    recipient: str | None = Field(default=None, max_length=320)
    other_agency: str | None = Field(default=None, max_length=200)


class StageIn(BaseModel):
    stage: PlacementStage
    interview_at: datetime | None = None
    offer_at: datetime | None = None
    start_date: date | None = None
    reason: str | None = Field(default=None, max_length=1000)


class FeedbackIn(BaseModel):
    decision: ClientDecision
    comment: str | None = Field(default=None, max_length=2000)


def _agent(services) -> ClientSubmissionAgent:
    return require_platform(services).submissions


def _out(services, submission: Submission) -> dict[str, Any]:
    candidate = services.platform.candidates.get(submission.candidate_id)
    return {**asdict(submission), "candidate_name": candidate.full_name if candidate else None}


def _client_out(services, submission: Submission) -> dict[str, Any]:
    """What the client was sent, and nothing internal (no match flags or approval data)."""
    job = services.platform.jobs.get(submission.job_id)
    sent = submission.sent_content
    return {
        "id": submission.id,
        "job_id": submission.job_id,
        "job_code": job.job_code if job else None,
        "job_title": job.title if job else None,
        "candidate_name": submission.package.get("candidate_name"),
        "sent_at": submission.sent_at,
        "subject": sent.get("subject"),
        "package": json.loads(sent.get("package") or "{}"),
        "client_decision": submission.client_decision,
        "client_feedback": submission.client_feedback,
        "stage": submission.stage,
        "interview_at": submission.interview_at,
        "start_date": submission.start_date,
    }


def _errors(exc: SubmissionError) -> HTTPException:
    if isinstance(exc, UnknownSubmission):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Submission, job or candidate not found")
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post("/jobs/{job_id}/submissions")
def prepare(job_id: str, body: PrepareIn, user: UserDep, services: ServicesDep) -> dict:
    """Agent 10 match report and the Agent 11 draft package for the recruiter to review."""
    require_role(user, TALENT)
    try:
        submission = _agent(services).prepare(job_id, body.candidate_id, user.user_id)
    except SubmissionError as exc:
        raise _errors(exc) from exc
    return _out(services, submission)


@router.get("/jobs/{job_id}/submissions")
def list_for_job(job_id: str, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, TALENT)
    agent = _agent(services)
    return {
        "summary": agent.summary(job_id),
        "submissions": [_out(services, s) for s in agent.list(job_id=job_id)],
    }


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: str, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, TALENT)
    try:
        return _out(services, _agent(services).get(submission_id))
    except SubmissionError as exc:
        raise _errors(exc) from exc


@router.get("/submissions/{submission_id}/package")
def package_document(
    submission_id: str,
    user: UserDep,
    services: ServicesDep,
    format: str = Query(default="pdf", pattern="^(pdf|docx)$"),
) -> Response:
    """The branded package exactly as the client will receive it (from the approval request's
    content once it is sent for approval; from the current draft before that)."""
    from recruiting.outreach.packages import MIME, render

    require_role(user, TALENT)
    agent = _agent(services)
    try:
        submission = agent.get(submission_id)
    except SubmissionError as exc:
        raise _errors(exc) from exc
    if submission.approval_request_id:
        content = services.approvals.get(submission.approval_request_id).content
    else:
        content = agent.cover_email(submission, user.display_name)
    document = render(content, format, services.platform.branding)  # type: ignore[arg-type]
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This submission has no package")
    return Response(
        document.data,
        media_type=MIME[format],
        headers={"Content-Disposition": f'attachment; filename="{document.filename}"'},
    )


@router.patch("/submissions/{submission_id}")
def edit(submission_id: str, body: EditIn, user: UserDep, services: ServicesDep) -> dict:
    """The recruiter edits the notes and package before approval."""
    require_role(user, TALENT)
    changes = body.model_dump(exclude_none=True)
    try:
        return _out(services, _agent(services).edit(submission_id, changes, user.user_id))
    except SubmissionError as exc:
        raise _errors(exc) from exc


@router.post("/submissions/{submission_id}/request-approval")
def request_approval(
    submission_id: str, body: ApprovalIn, user: UserDep, services: ServicesDep
) -> dict:
    """Starts the "resume submission" gate (BRD section 10). Sent only once approved."""
    require_role(user, SENDERS)
    try:
        submission = _agent(services).request_approval(
            submission_id, ApprovalInput(**body.model_dump()), user.user_id, user.display_name
        )
    except SubmissionError as exc:
        raise _errors(exc) from exc
    return _out(services, submission)


@router.post("/submissions/{submission_id}/withdraw")
def withdraw(submission_id: str, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, SENDERS)
    try:
        return _out(services, _agent(services).withdraw(submission_id, user.user_id))
    except SubmissionError as exc:
        raise _errors(exc) from exc


@router.post("/submissions/{submission_id}/stage")
def set_stage(submission_id: str, body: StageIn, user: UserDep, services: ServicesDep) -> dict:
    """Pipeline after submission: interview, offer, start, or rejected / withdrawn. A withdrawal
    returns the next-ranked candidates to consider (scenario S21)."""
    require_role(user, TALENT)
    try:
        submission, suggestions = _agent(services).set_stage(
            submission_id, body.stage, user.user_id, interview_at=body.interview_at,
            offer_at=body.offer_at, start_date=body.start_date, reason=body.reason,
        )  # fmt: skip
    except SubmissionError as exc:
        raise _errors(exc) from exc
    return {**_out(services, submission), "suggestions": suggestions}


# ----- client portal (Q8) ----------------------------------------------------------------------


@router.get("/client/submissions")
def client_submissions(user: UserDep, services: ServicesDep) -> list[dict]:
    require_role(user, (Role.CLIENT,))
    agent = _agent(services)
    return [
        _client_out(services, s)
        for s in agent.list(client_company_id=user.client_company_id)
        if s.sent_at is not None
    ]


@router.post("/client/submissions/{submission_id}/feedback")
def client_feedback(
    submission_id: str, body: FeedbackIn, user: UserDep, services: ServicesDep
) -> dict:
    require_role(user, (Role.CLIENT,))
    try:
        submission = _agent(services).record_client_feedback(
            submission_id, user.client_company_id or "", body.decision, body.comment,
            user.user_id,
        )  # fmt: skip
    except SubmissionError as exc:
        raise _errors(exc) from exc
    return _client_out(services, submission)
