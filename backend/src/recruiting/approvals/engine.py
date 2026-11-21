"""Approval-gate engine (BRD section 10, plan section 5.3).

An agent submits a draft; the role named by the gate policy approves, edits or rejects it.
Nothing is sent until the request is APPROVED, and the send must match the approved content
exactly (checked by hash), so a draft cannot change after approval.

Edit rule: only the first approver may edit. A second approver sees the final content and
approves or rejects it, so every approval in the chain covers the same version.

This in-memory engine defines the behaviour; the PostgreSQL store (db/migrations) persists
the same records.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from recruiting.approvals.policy import DEFAULT_POLICIES, GatePolicy
from recruiting.domain import GateType, Role

Content = Mapping[str, str]


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DecisionAction(StrEnum):
    APPROVE = "approve"
    EDIT_AND_APPROVE = "edit_and_approve"
    REJECT = "reject"


class ApprovalError(Exception):
    pass


class NotAuthorizedToApprove(ApprovalError):
    pass


class InvalidDecision(ApprovalError):
    pass


class NotApproved(ApprovalError):
    pass


def content_hash(content: Content) -> str:
    canonical = json.dumps(dict(content), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Reviewer:
    user_id: str
    role: Role


@dataclass(frozen=True)
class Decision:
    reviewer: Reviewer
    action: DecisionAction
    content_sha256: str
    reason: str | None
    decided_at: datetime


@dataclass
class ApprovalRequest:
    id: str
    gate: GateType
    subject_id: str
    requested_by: str
    content: dict[str, str]
    created_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    decisions: list[Decision] = field(default_factory=list)

    @property
    def content_sha256(self) -> str:
        return content_hash(self.content)

    @property
    def approval_count(self) -> int:
        return sum(1 for d in self.decisions if d.action is not DecisionAction.REJECT)


class ApprovalEngine:
    def __init__(
        self,
        policies: Mapping[GateType, GatePolicy] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._policies = dict(DEFAULT_POLICIES if policies is None else policies)
        missing = set(GateType) - set(self._policies)
        if missing:
            raise ValueError(f"No approval policy for gates: {sorted(missing)}")
        self._requests: dict[str, ApprovalRequest] = {}
        self._clock = clock

    def policy_for(self, gate: GateType) -> GatePolicy:
        return self._policies[gate]

    def submit(
        self, gate: GateType, subject_id: str, content: Content, requested_by: str
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            id=str(uuid.uuid4()),
            gate=gate,
            subject_id=subject_id,
            requested_by=requested_by,
            content=dict(content),
            created_at=self._clock(),
        )
        self._requests[request.id] = request
        return request

    def get(self, request_id: str) -> ApprovalRequest:
        try:
            return self._requests[request_id]
        except KeyError:
            raise ApprovalError(f"Unknown approval request {request_id}") from None

    def next_approver_role(self, request: ApprovalRequest) -> Role | None:
        if request.status is not ApprovalStatus.PENDING:
            return None
        steps = self.policy_for(request.gate).steps
        return steps[request.approval_count] if request.approval_count < len(steps) else None

    def pending_for(self, role: Role) -> list[ApprovalRequest]:
        return [r for r in self._requests.values() if self.next_approver_role(r) == role]

    def decide(
        self,
        request_id: str,
        reviewer: Reviewer,
        action: DecisionAction,
        *,
        reason: str | None = None,
        edited_content: Content | None = None,
    ) -> ApprovalRequest:
        request = self.get(request_id)
        if request.status is not ApprovalStatus.PENDING:
            raise InvalidDecision(f"Request is already {request.status}")

        required = self.next_approver_role(request)
        if reviewer.role != required:
            raise NotAuthorizedToApprove(f"This step needs role {required}, not {reviewer.role}")
        if reviewer.user_id == request.requested_by:
            raise NotAuthorizedToApprove("A requester cannot approve their own request")
        if any(d.reviewer.user_id == reviewer.user_id for d in request.decisions):
            raise NotAuthorizedToApprove("The second approver must be a different person")

        if action is DecisionAction.REJECT:
            if not reason or not reason.strip():
                raise InvalidDecision("A rejection needs a reason")
            request.status = ApprovalStatus.REJECTED
        elif action is DecisionAction.EDIT_AND_APPROVE:
            if request.approval_count > 0:
                raise InvalidDecision("Only the first approver can edit; approve or reject instead")
            if edited_content is None or dict(edited_content) == request.content:
                raise InvalidDecision("Edit-and-approve needs changed content")
            request.content = dict(edited_content)
        elif edited_content is not None:
            raise InvalidDecision("Use EDIT_AND_APPROVE to change the content")

        request.decisions.append(
            Decision(
                reviewer=reviewer,
                action=action,
                content_sha256=request.content_sha256,
                reason=reason,
                decided_at=self._clock(),
            )
        )
        if request.status is ApprovalStatus.PENDING and self.next_approver_role(request) is None:
            request.status = ApprovalStatus.APPROVED
        return request

    def assert_approved(self, request_id: str, content: Content) -> ApprovalRequest:
        """Hard block used by every send / submit path."""
        request = self.get(request_id)
        if request.status is not ApprovalStatus.APPROVED:
            raise NotApproved(f"Approval request {request_id} is {request.status}")
        if content_hash(content) != request.content_sha256:
            raise NotApproved("Content differs from the approved version")
        return request
