"""Approval-gate engine (BRD section 10, plan section 5.3).

An agent submits a draft; the role named by the gate policy approves, edits or rejects it.
Nothing is sent until the request is APPROVED, and the send must match the approved content
exactly (checked by hash), so a draft cannot change after approval.

Edit rule: only the first approver may edit. A second approver sees the final content and
approves or rejects it, so every approval in the chain covers the same version.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from recruiting.approvals.policy import (
    DEFAULT_POLICIES,
    GatePolicy,
    InMemoryPolicyStore,
    PolicyStore,
)
from recruiting.approvals.store import ApprovalStore, InMemoryApprovalStore
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
    # The request no longer applies (e.g. its job was closed); recorded like a rejection.
    CANCEL = "cancel"


class ApprovalError(Exception):
    pass


class UnknownApprovalRequest(ApprovalError):
    pass


class NotAuthorizedToApprove(ApprovalError):
    pass


class InvalidDecision(ApprovalError):
    pass


class NotApproved(ApprovalError):
    pass


class InvalidPolicy(ApprovalError):
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
    resolved_at: datetime | None = None

    @property
    def content_sha256(self) -> str:
        return content_hash(self.content)

    @property
    def approval_count(self) -> int:
        return sum(
            1
            for d in self.decisions
            if d.action in (DecisionAction.APPROVE, DecisionAction.EDIT_AND_APPROVE)
        )


class ApprovalEngine:
    def __init__(
        self,
        policies: Mapping[GateType, GatePolicy] | PolicyStore | None = None,
        store: ApprovalStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        # Policies are read from the store on use, so an Administrator's change reaches every
        # running instance without a restart.
        if policies is None:
            policies = DEFAULT_POLICIES
        self._policies: PolicyStore = (
            InMemoryPolicyStore(policies) if isinstance(policies, Mapping) else policies
        )
        missing = set(GateType) - set(self._policies.load())
        if missing:
            raise ValueError(f"No approval policy for gates: {sorted(missing)}")
        self._store = InMemoryApprovalStore() if store is None else store
        self._clock = clock

    def policies(self) -> dict[GateType, GatePolicy]:
        return self._policies.load()

    def policy_for(self, gate: GateType) -> GatePolicy:
        return self._policies.load()[gate]

    def update_policy(self, policy: GatePolicy, updated_by: str) -> GatePolicy:
        """Q14: the Administrator changes who approves a gate. Pending requests follow the new
        policy from their next step; decisions already made stay valid."""
        for role in policy.steps:
            if role is Role.CLIENT:
                raise InvalidPolicy("Client users cannot approve platform actions")
        self._policies.save(policy, updated_by)
        return policy

    def submit(
        self,
        gate: GateType,
        subject_id: str,
        content: Content,
        requested_by: str,
        *,
        request_id: str | None = None,
    ) -> ApprovalRequest:
        """Create a pending request. With `request_id`, submitting again returns the existing
        request, so a workflow step that re-runs after a crash does not create a duplicate."""
        if request_id is not None:
            existing = self._store.get(request_id)
            if existing is not None:
                return existing
        request = ApprovalRequest(
            id=request_id or str(uuid.uuid4()),
            gate=gate,
            subject_id=subject_id,
            requested_by=requested_by,
            content=dict(content),
            created_at=self._clock(),
        )
        self._store.add(request)
        return request

    def get(self, request_id: str) -> ApprovalRequest:
        request = self._store.get(request_id)
        if request is None:
            raise UnknownApprovalRequest(f"Unknown approval request {request_id}")
        return request

    def next_approver_role(
        self, request: ApprovalRequest, policies: Mapping[GateType, GatePolicy] | None = None
    ) -> Role | None:
        if request.status is not ApprovalStatus.PENDING:
            return None
        policy = policies[request.gate] if policies is not None else self.policy_for(request.gate)
        steps = policy.steps
        return steps[request.approval_count] if request.approval_count < len(steps) else None

    def pending_for(self, role: Role) -> list[ApprovalRequest]:
        policies = self.policies()
        return [r for r in self._store.pending() if self.next_approver_role(r, policies) == role]

    def decide(
        self,
        request_id: str,
        reviewer: Reviewer,
        action: DecisionAction,
        *,
        reason: str | None = None,
        edited_content: Content | None = None,
    ) -> ApprovalRequest:
        if action is DecisionAction.CANCEL:
            raise InvalidDecision("Use cancel() to withdraw a request")
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

        new_content = request.content
        if action is DecisionAction.REJECT:
            if not reason or not reason.strip():
                raise InvalidDecision("A rejection needs a reason")
        elif action is DecisionAction.EDIT_AND_APPROVE:
            if request.approval_count > 0:
                raise InvalidDecision("Only the first approver can edit; approve or reject instead")
            if edited_content is None or dict(edited_content) == request.content:
                raise InvalidDecision("Edit-and-approve needs changed content")
            new_content = dict(edited_content)
        elif edited_content is not None:
            raise InvalidDecision("Use EDIT_AND_APPROVE to change the content")

        # All checks passed: apply the change, then persist it atomically.
        prior_decisions = len(request.decisions)
        now = self._clock()
        request.content = new_content
        decision = Decision(
            reviewer=reviewer,
            action=action,
            content_sha256=request.content_sha256,
            reason=reason,
            decided_at=now,
        )
        request.decisions.append(decision)
        if action is DecisionAction.REJECT:
            request.status = ApprovalStatus.REJECTED
        elif self.next_approver_role(request) is None:
            request.status = ApprovalStatus.APPROVED
        if request.status is not ApprovalStatus.PENDING:
            request.resolved_at = now

        self._store.record_decision(request, decision, prior_decisions)
        return request

    def cancel(self, request_id: str, by: Reviewer, reason: str) -> ApprovalRequest:
        """Withdraw a pending request whose subject no longer applies (scenario S22: the job
        was filled or closed). Not an approval: the request ends REJECTED and nothing is sent.
        The person closing the job is recorded, whatever the gate's approver role."""
        if not reason.strip():
            raise InvalidDecision("A cancellation needs a reason")
        request = self.get(request_id)
        if request.status is not ApprovalStatus.PENDING:
            return request
        prior_decisions = len(request.decisions)
        now = self._clock()
        decision = Decision(by, DecisionAction.CANCEL, request.content_sha256, reason, now)
        request.decisions.append(decision)
        request.status = ApprovalStatus.REJECTED
        request.resolved_at = now
        self._store.record_decision(request, decision, prior_decisions)
        return request

    def assert_approved(self, request_id: str, content: Content) -> ApprovalRequest:
        """Hard block used by every send / submit path."""
        request = self.get(request_id)
        if request.status is not ApprovalStatus.APPROVED:
            raise NotApproved(f"Approval request {request_id} is {request.status}")
        if content_hash(content) != request.content_sha256:
            raise NotApproved("Content differs from the approved version")
        return request
