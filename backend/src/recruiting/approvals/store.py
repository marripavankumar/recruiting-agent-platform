"""Storage interface for approval requests. In-memory for tests and local; PostgreSQL otherwise."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from recruiting.approvals.engine import ApprovalRequest, Decision


class ConcurrentDecision(Exception):
    """Another reviewer decided this step first; reload and try again."""


class ApprovalStore(Protocol):
    def add(self, request: ApprovalRequest) -> None: ...

    def get(self, request_id: str) -> ApprovalRequest | None: ...

    def record_decision(
        self, request: ApprovalRequest, decision: Decision, expected_prior_decisions: int
    ) -> None:
        """Persist the request's new content and status plus the decision, atomically.

        Must raise ConcurrentDecision if the stored request is no longer pending or already has
        a different number of decisions than `expected_prior_decisions`.
        """
        ...

    def pending(self) -> list[ApprovalRequest]: ...


class InMemoryApprovalStore:
    """Holds live objects; the engine mutates them in place. Single-process use only."""

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}

    def add(self, request: ApprovalRequest) -> None:
        self._requests[request.id] = request

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._requests.get(request_id)

    def record_decision(
        self, request: ApprovalRequest, decision: Decision, expected_prior_decisions: int
    ) -> None:
        self._requests[request.id] = request

    def pending(self) -> list[ApprovalRequest]:
        return [r for r in self._requests.values() if r.status == "pending"]
