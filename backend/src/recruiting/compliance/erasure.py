"""Candidate data erasure (scenario S23; compliance checklist, data subject requests).

`erase` anonymises the candidate in place rather than deleting the row, so job, ranking and
submission statistics stay consistent while no personal data remains:
- the Candidate record keeps only its id and the erasure date;
- the candidate's email and LinkedIn URL go on the opt-out (suppression) list, so no agent or
  re-import contacts them again;
- their engagements lose the upload link, consent evidence and are set to do-not-contact;
- submission packages and match reports are replaced by a placeholder;
- open follow-ups are cancelled; the search index entry is removed.

Approval requests and the delivery log keep the exact text that was approved and sent, as the
audit trail BRD section 10 requires. They are reported back (`audit_records_kept`) so the
retention policy decides how long they stay (compliance checklist, legal review).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from recruiting.ai.search import SemanticSearch
from recruiting.compliance import OptOutRegistry
from recruiting.records.engagements import EngagementStore
from recruiting.records.followups import FollowUpStore
from recruiting.records.memory import CandidateRepository
from recruiting.records.models import (
    CLOSED_STAGES,
    Candidate,
    ConsentStatus,
    EngagementStatus,
    FollowUpStatus,
    PlacementStage,
)
from recruiting.records.submissions import SubmissionStore

logger = logging.getLogger("recruiting.audit")
ERASED = "Erased candidate"


class ErasureError(Exception):
    pass


class CandidateErasure:
    def __init__(
        self,
        candidates: CandidateRepository,
        engagements: EngagementStore,
        submissions: SubmissionStore,
        follow_ups: FollowUpStore,
        search: SemanticSearch,
        opt_outs: OptOutRegistry,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        documents: Any = None,
    ) -> None:
        self._documents = documents  # storage.DocumentStore: original resume files
        self._candidates = candidates
        self._engagements = engagements
        self._submissions = submissions
        self._follow_ups = follow_ups
        self._search = search
        self._opt_outs = opt_outs
        self._clock = clock

    def erase(self, candidate_id: str, by: str, reason: str) -> dict[str, Any]:
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        if candidate.source == "erased":
            raise ErasureError("This candidate was already erased")
        now = self._clock()
        for address in (candidate.email, candidate.linkedin_url):
            if address:
                self._opt_outs.record(address, source="erasure_request")
        approvals = 0

        self._candidates.save(
            Candidate(candidate.id, ERASED, "erased", consent_status=ConsentStatus.WITHDRAWN)
        )
        engagements = self._engagements.for_candidate(candidate.id)
        for e in engagements:
            approvals += len(e.approvals)
            e.token_hash = e.token_expires_at = None
            e.consent_evidence = "erased"
            e.missing, e.next_reminder_at = [], None
            if e.status is not EngagementStatus.SUBMITTED:
                e.status = EngagementStatus.DO_NOT_CONTACT
            e.history = [{"at": now.isoformat(), "event": "erased"}]
            e.updated_at = now
            self._engagements.save(e)
        submissions = self._submissions.list(candidate_id=candidate.id)
        for s in submissions:
            approvals += s.approval_request_id is not None
            s.package = {"candidate_name": ERASED}
            s.match = {}
            s.sent_content = {"erased": now.isoformat()} if s.sent_content else {}
            s.client_feedback = None
            if s.stage is not None and s.stage not in CLOSED_STAGES:
                s.stage, s.stage_at = PlacementStage.CANDIDATE_WITHDRAWN, now
                s.stage_reason = "Candidate data erased on request"
            s.log(now, "erased", by=by)
            self._submissions.save(s)
        cancelled = 0
        for item in self._follow_ups.list(status=FollowUpStatus.OPEN):
            if item.candidate_id == candidate.id:
                item.status, item.due_at, item.context, item.note = (
                    FollowUpStatus.CANCELLED, None, {}, ERASED,
                )  # fmt: skip
                item.log(now, "cancelled", reason="erasure")
                self._follow_ups.save(item)
                cancelled += 1
        self._search.remove("candidate", candidate.id)
        files = (
            self._documents.delete_prefix(f"candidates/{candidate.id}/")
            if self._documents is not None
            else 0
        )
        record = {
            "candidate_id": candidate.id,
            "erased_at": now,
            "by": by,
            "reason": reason,
            "engagements": len(engagements),
            "submissions": len(submissions),
            "follow_ups_cancelled": cancelled,
            "files_deleted": files,
            "audit_records_kept": approvals,
        }
        logger.warning("candidate_erased %s", {**record, "erased_at": now.isoformat()})
        return record
