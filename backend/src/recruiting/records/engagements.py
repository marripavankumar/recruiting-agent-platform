"""Storage for candidate engagements (Agents 8-9)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Protocol

from recruiting.records.models import Engagement, EngagementStatus


class EngagementStore(Protocol):
    def get(self, engagement_id: str) -> Engagement | None: ...

    def find(self, job_id: str, candidate_id: str) -> Engagement | None: ...

    def by_token_hash(self, token_hash: str) -> Engagement | None: ...

    def by_approval(self, approval_request_id: str) -> Engagement | None: ...

    def for_candidate(self, candidate_id: str) -> list[Engagement]: ...

    def list(
        self, job_id: str | None = None, status: EngagementStatus | None = None
    ) -> list[Engagement]: ...

    def save(self, engagement: Engagement) -> Engagement: ...


class InMemoryEngagements:
    def __init__(self) -> None:
        self._items: dict[str, Engagement] = {}

    def _first(self, predicate) -> Engagement | None:
        return next((copy.deepcopy(e) for e in self._items.values() if predicate(e)), None)

    def get(self, engagement_id: str) -> Engagement | None:
        item = self._items.get(engagement_id)
        return copy.deepcopy(item) if item else None

    def find(self, job_id: str, candidate_id: str) -> Engagement | None:
        return self._first(lambda e: e.job_id == job_id and e.candidate_id == candidate_id)

    def by_token_hash(self, token_hash: str) -> Engagement | None:
        return self._first(lambda e: e.token_hash == token_hash)

    def by_approval(self, approval_request_id: str) -> Engagement | None:
        return self._first(lambda e: approval_request_id in e.approvals.values())

    def for_candidate(self, candidate_id: str) -> list[Engagement]:
        items = [copy.deepcopy(e) for e in self._items.values() if e.candidate_id == candidate_id]
        oldest = datetime.min.replace(tzinfo=UTC)
        return sorted(items, key=lambda e: e.updated_at or oldest, reverse=True)

    def list(
        self, job_id: str | None = None, status: EngagementStatus | None = None
    ) -> list[Engagement]:
        return [
            copy.deepcopy(e)
            for e in self._items.values()
            if (job_id is None or e.job_id == job_id) and (status is None or e.status == status)
        ]

    def save(self, engagement: Engagement) -> Engagement:
        self._items[engagement.id] = copy.deepcopy(engagement)
        return engagement
