"""Storage for client submissions (Agents 10-11)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Protocol

from recruiting.records.models import Submission, SubmissionStatus


class SubmissionStore(Protocol):
    def get(self, submission_id: str) -> Submission | None: ...

    def by_approval(self, approval_request_id: str) -> Submission | None: ...

    def list(
        self,
        job_id: str | None = None,
        candidate_id: str | None = None,
        client_company_id: str | None = None,
        status: SubmissionStatus | None = None,
    ) -> list[Submission]: ...

    def save(self, submission: Submission) -> Submission: ...


class InMemorySubmissions:
    def __init__(self) -> None:
        self._items: dict[str, Submission] = {}

    def get(self, submission_id: str) -> Submission | None:
        item = self._items.get(submission_id)
        return copy.deepcopy(item) if item else None

    def by_approval(self, approval_request_id: str) -> Submission | None:
        return next(
            (
                copy.deepcopy(s)
                for s in self._items.values()
                if s.approval_request_id == approval_request_id
            ),
            None,
        )

    def list(
        self,
        job_id: str | None = None,
        candidate_id: str | None = None,
        client_company_id: str | None = None,
        status: SubmissionStatus | None = None,
    ) -> list[Submission]:
        items = [
            copy.deepcopy(s)
            for s in self._items.values()
            if (job_id is None or s.job_id == job_id)
            and (candidate_id is None or s.candidate_id == candidate_id)
            and (client_company_id is None or s.client_company_id == client_company_id)
            and (status is None or s.status == status)
        ]
        oldest = datetime.min.replace(tzinfo=UTC)
        return sorted(items, key=lambda s: s.created_at or oldest, reverse=True)

    def save(self, submission: Submission) -> Submission:
        self._items[submission.id] = copy.deepcopy(submission)
        return submission
