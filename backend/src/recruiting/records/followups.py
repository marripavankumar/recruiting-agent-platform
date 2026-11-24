"""Storage for Agent 12 follow-ups and the follow-up cadence (Q13)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Protocol

from recruiting.records.models import FollowUp, FollowUpKind, FollowUpStatus

# Q13 default: 3 follow-ups at 3, 7 and 14 days, then stop. Days count from the item's anchor
# (the send, the request, the interview, the start date ...); negative means "before".
DEFAULT_CADENCE: dict[FollowUpKind, list[int]] = {
    FollowUpKind.CLIENT_OUTREACH: [3, 7, 14],
    FollowUpKind.SUBMISSION_FEEDBACK: [3, 7, 14],
    FollowUpKind.CANDIDATE_OUTREACH: [3, 7, 14],
    FollowUpKind.CANDIDATE_DETAILS: [3, 7, 14],
    FollowUpKind.INTERVIEW_SCHEDULING: [1, 2, 4],
    FollowUpKind.INTERVIEW_PREP: [-1],
    FollowUpKind.INTERVIEW_FEEDBACK: [1, 3, 7],
    FollowUpKind.OFFER: [2, 5, 10],
    FollowUpKind.START_DATE: [-7, -1, 7],
    FollowUpKind.CANDIDATE_SHORTFALL: [0, 2, 5],
}


class InvalidCadence(ValueError):
    pass


def validate_cadence(days: list[int]) -> list[int]:
    if not 1 <= len(days) <= 5:
        raise InvalidCadence("Give between 1 and 5 follow-ups")
    if any(not -60 <= d <= 90 for d in days) or days != sorted(set(days)):
        raise InvalidCadence("Days must be distinct, ascending and between -60 and 90")
    return list(days)


class FollowUpStore(Protocol):
    def get(self, follow_up_id: str) -> FollowUp | None: ...

    def find(self, kind: FollowUpKind, subject_id: str) -> FollowUp | None: ...

    def list(
        self, status: FollowUpStatus | None = None, job_id: str | None = None
    ) -> list[FollowUp]: ...

    def save(self, item: FollowUp) -> FollowUp: ...


class CadenceStore(Protocol):
    def get(self) -> dict[FollowUpKind, list[int]]: ...

    def save(self, kind: FollowUpKind, days: list[int], by: str) -> None: ...


class InMemoryFollowUps:
    def __init__(self) -> None:
        self._items: dict[str, FollowUp] = {}

    def get(self, follow_up_id: str) -> FollowUp | None:
        item = self._items.get(follow_up_id)
        return copy.deepcopy(item) if item else None

    def find(self, kind: FollowUpKind, subject_id: str) -> FollowUp | None:
        return next(
            (
                copy.deepcopy(f)
                for f in self._items.values()
                if f.kind == kind and f.subject_id == subject_id
            ),
            None,
        )

    def list(
        self, status: FollowUpStatus | None = None, job_id: str | None = None
    ) -> list[FollowUp]:
        far = datetime.max.replace(tzinfo=UTC)
        items = [
            copy.deepcopy(f)
            for f in self._items.values()
            if (status is None or f.status == status) and (job_id is None or f.job_id == job_id)
        ]
        return sorted(items, key=lambda f: f.due_at or far)

    def save(self, item: FollowUp) -> FollowUp:
        self._items[item.id] = copy.deepcopy(item)
        return item


class InMemoryCadence:
    def __init__(self) -> None:
        self._days = {k: list(v) for k, v in DEFAULT_CADENCE.items()}

    def get(self) -> dict[FollowUpKind, list[int]]:
        return {k: list(v) for k, v in self._days.items()}

    def save(self, kind: FollowUpKind, days: list[int], by: str) -> None:
        self._days[kind] = validate_cadence(days)
