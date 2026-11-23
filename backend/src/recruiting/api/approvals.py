"""Review-queue API for the four approval gates (BRD section 10, plan section 5.3)."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel

from recruiting.api.deps import ServicesDep, UserDep
from recruiting.api.services import WorkflowResumer
from recruiting.approvals import (
    ApprovalEngine,
    ApprovalRequest,
    ApprovalStatus,
    ConcurrentDecision,
    DecisionAction,
    InvalidDecision,
    NotAuthorizedToApprove,
    Reviewer,
    UnknownApprovalRequest,
)
from recruiting.domain import Role
from recruiting.identity import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/approvals", tags=["approvals"])


class DecisionOut(BaseModel):
    reviewer_id: str
    reviewer_role: Role
    action: DecisionAction
    reason: str | None
    decided_at: datetime


class ApprovalOut(BaseModel):
    id: str
    gate: str
    subject_id: str
    requested_by: str
    content: dict[str, str]
    status: str
    next_approver_role: Role | None
    created_at: datetime
    resolved_at: datetime | None
    decisions: list[DecisionOut]


class DecisionIn(BaseModel):
    action: DecisionAction
    reason: str | None = None
    edited_content: dict[str, str] | None = None


def to_out(engine: ApprovalEngine, request: ApprovalRequest) -> ApprovalOut:
    return ApprovalOut(
        id=request.id,
        gate=request.gate.value,
        subject_id=request.subject_id,
        requested_by=request.requested_by,
        content=request.content,
        status=request.status.value,
        next_approver_role=engine.next_approver_role(request),
        created_at=request.created_at,
        resolved_at=request.resolved_at,
        decisions=[
            DecisionOut(
                reviewer_id=d.reviewer.user_id,
                reviewer_role=d.reviewer.role,
                action=d.action,
                reason=d.reason,
                decided_at=d.decided_at,
            )
            for d in request.decisions
        ],
    )


def _require_staff(user: CurrentUser) -> None:
    if user.role is Role.CLIENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Clients have no approval queue")


def _can_view(engine: ApprovalEngine, user: CurrentUser, request: ApprovalRequest) -> bool:
    return user.role is Role.ADMINISTRATOR or user.role in engine.policy_for(request.gate).steps


@router.get("/pending", response_model=list[ApprovalOut])
def list_pending(user: UserDep, services: ServicesDep) -> list[ApprovalOut]:
    """Requests waiting for the signed-in user's role."""
    _require_staff(user)
    engine = services.approvals
    return [to_out(engine, r) for r in engine.pending_for(user.role)]


@router.get("/{request_id}", response_model=ApprovalOut)
def get_approval(request_id: str, user: UserDep, services: ServicesDep) -> ApprovalOut:
    _require_staff(user)
    engine = services.approvals
    try:
        request = engine.get(request_id)
    except UnknownApprovalRequest:
        request = None
    # Requests outside the user's gates are reported as not found, so their existence is hidden.
    if request is None or not _can_view(engine, user, request):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found")
    return to_out(engine, request)


@router.post("/{request_id}/decision", response_model=ApprovalOut)
def decide(
    request_id: str,
    body: DecisionIn,
    user: UserDep,
    services: ServicesDep,
    background: BackgroundTasks,
) -> ApprovalOut:
    _require_staff(user)
    engine = services.approvals
    try:
        request = engine.decide(
            request_id,
            Reviewer(user.user_id, user.role),
            body.action,
            reason=body.reason,
            edited_content=body.edited_content,
        )
    except UnknownApprovalRequest as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found") from exc
    except NotAuthorizedToApprove as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except (InvalidDecision, ConcurrentDecision) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if request.status is not ApprovalStatus.PENDING and services.workflows is not None:
        # After the response: the decision is already saved, so a delivery error cannot undo it.
        background.add_task(_resume_workflow, services.workflows, request.id)
    return to_out(engine, request)


def _resume_workflow(workflows: WorkflowResumer, request_id: str) -> None:
    try:
        workflows.resume(request_id)
    except Exception:
        # The thread stays paused at its last checkpoint and can be resumed again later.
        logger.exception("Resuming the workflow for approval %s failed", request_id)
