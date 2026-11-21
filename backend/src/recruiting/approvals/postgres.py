"""PostgreSQL persistence for approval requests and gate policies (db/migrations 0001, 0002)."""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from recruiting.approvals.engine import (
    ApprovalRequest,
    ApprovalStatus,
    Decision,
    DecisionAction,
    Reviewer,
)
from recruiting.approvals.policy import GatePolicy
from recruiting.approvals.store import ConcurrentDecision
from recruiting.db import Connect, is_uuid
from recruiting.domain import GateType, Role

SUBJECT_TYPES: dict[GateType, str] = {
    GateType.FIRST_CLIENT_EMAIL: "company_contact",
    GateType.LINKEDIN_CONNECTION_REQUEST: "candidate",
    GateType.CANDIDATE_OUTREACH_EMAIL: "candidate",
    GateType.RESUME_SUBMISSION: "candidate",
}


def load_gate_policies(connect: Connect) -> dict[GateType, GatePolicy]:
    """Q14: the Administrator edits approvers per gate in the gate_policy table."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT gate, first_approver_role, second_approver_role FROM gate_policy"
        ).fetchall()
    return {
        GateType(gate): GatePolicy(GateType(gate), Role(first), Role(second) if second else None)
        for gate, first, second in rows
    }


class PostgresApprovalStore:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def add(self, request: ApprovalRequest) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO approval_request
                    (id, gate, subject_type, subject_id, requested_by, content, content_sha256,
                     status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    request.id,
                    request.gate.value,
                    SUBJECT_TYPES[request.gate],
                    request.subject_id,
                    request.requested_by,
                    Jsonb(request.content),
                    request.content_sha256,
                    request.status.value,
                    request.created_at,
                ),
            )

    def get(self, request_id: str) -> ApprovalRequest | None:
        if not is_uuid(request_id):
            return None
        with self._connect() as conn:
            return self._load(conn, request_id)

    def pending(self) -> list[ApprovalRequest]:
        with self._connect() as conn:
            ids = conn.execute(
                "SELECT id::text FROM approval_request WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
            loaded = (self._load(conn, request_id) for (request_id,) in ids)
            return [r for r in loaded if r is not None]

    def record_decision(
        self, request: ApprovalRequest, decision: Decision, expected_prior_decisions: int
    ) -> None:
        with self._connect() as conn:
            # Lock the request row so two reviewers cannot decide the same step at once.
            row = conn.execute(
                "SELECT status FROM approval_request WHERE id = %s FOR UPDATE", (request.id,)
            ).fetchone()
            (prior,) = conn.execute(
                "SELECT count(*) FROM approval_decision WHERE approval_request_id = %s",
                (request.id,),
            ).fetchone()
            if row is None or row[0] != ApprovalStatus.PENDING or prior != expected_prior_decisions:
                raise ConcurrentDecision(f"Approval request {request.id} changed; reload it")

            conn.execute(
                """
                UPDATE approval_request
                SET content = %s, content_sha256 = %s, status = %s, resolved_at = %s
                WHERE id = %s
                """,
                (
                    Jsonb(request.content),
                    request.content_sha256,
                    request.status.value,
                    request.resolved_at,
                    request.id,
                ),
            )
            conn.execute(
                """
                INSERT INTO approval_decision
                    (approval_request_id, reviewer_id, reviewer_role, action, content_sha256,
                     reason, decided_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    request.id,
                    decision.reviewer.user_id,
                    decision.reviewer.role.value,
                    decision.action.value,
                    decision.content_sha256,
                    decision.reason,
                    decision.decided_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO audit_log (actor, action, entity_type, entity_id, details)
                VALUES (%s, %s, 'approval_request', %s, %s)
                """,
                (
                    decision.reviewer.user_id,
                    f"approval.{decision.action.value}",
                    request.id,
                    Jsonb({"gate": request.gate.value, "status": request.status.value}),
                ),
            )

    @staticmethod
    def _load(conn: psycopg.Connection, request_id: str) -> ApprovalRequest | None:
        row = conn.execute(
            """
            SELECT id::text, gate, subject_id, requested_by, content, status, created_at,
                   resolved_at
            FROM approval_request WHERE id = %s
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        rid, gate, subject_id, requested_by, content, status, created_at, resolved_at = row
        decision_rows = conn.execute(
            """
            SELECT reviewer_id::text, reviewer_role, action, content_sha256, reason, decided_at
            FROM approval_decision WHERE approval_request_id = %s
            ORDER BY decided_at, id
            """,
            (request_id,),
        ).fetchall()
        return ApprovalRequest(
            id=rid,
            gate=GateType(gate),
            subject_id=subject_id,
            requested_by=requested_by,
            content=dict(content),
            created_at=created_at,
            status=ApprovalStatus(status),
            resolved_at=resolved_at,
            decisions=[
                Decision(
                    reviewer=Reviewer(user_id, Role(role)),
                    action=DecisionAction(action),
                    content_sha256=sha,
                    reason=reason,
                    decided_at=decided_at,
                )
                for user_id, role, action, sha, reason, decided_at in decision_rows
            ],
        )
