"""Integration tests against a real PostgreSQL with db/migrations applied.

Set RAP_TEST_DATABASE_URL to run them (CI does). Skipped otherwise.
"""

import os
import uuid

import pytest

DATABASE_URL = os.environ.get("RAP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="RAP_TEST_DATABASE_URL not set")

from recruiting.approvals import (  # noqa: E402
    DEFAULT_POLICIES,
    ApprovalEngine,
    ApprovalStatus,
    ConcurrentDecision,
    Decision,
    DecisionAction,
    Reviewer,
    UnknownApprovalRequest,
)
from recruiting.domain import GateType, Role  # noqa: E402

DRAFT = {"subject": "Engineering Talent Support", "body": "Dear Ms Lee, ..."}


@pytest.fixture
def connect():
    from recruiting.db import connection_factory

    factory = connection_factory(DATABASE_URL)
    with factory() as conn:
        conn.execute("TRUNCATE approval_decision, approval_request, opt_out CASCADE")
    return factory


def make_user(connect, role: Role) -> Reviewer:
    with connect() as conn:
        (user_id,) = conn.execute(
            "INSERT INTO app_user (email, display_name, role) VALUES (%s, %s, %s)"
            " RETURNING id::text",
            (f"{uuid.uuid4()}@cbs.example", "Test user", role.value),
        ).fetchone()
    return Reviewer(user_id, role)


@pytest.fixture
def engine(connect):
    from recruiting.approvals.postgres import PostgresApprovalStore, load_gate_policies

    return ApprovalEngine(load_gate_policies(connect), store=PostgresApprovalStore(connect))


def test_gate_policies_seeded_with_q14_defaults(connect):
    from recruiting.approvals.postgres import load_gate_policies

    assert load_gate_policies(connect) == DEFAULT_POLICIES


def test_submit_decide_and_reload(engine, connect):
    bd = make_user(connect, Role.BD_MANAGER)
    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "contact-1", DRAFT, requested_by="agent-3")
    assert [r.id for r in engine.pending_for(Role.BD_MANAGER)] == [request.id]

    edited = {**DRAFT, "body": "Dear Ms Lee, edited"}
    engine.decide(request.id, bd, DecisionAction.EDIT_AND_APPROVE, edited_content=edited)

    reloaded = engine.get(request.id)
    assert reloaded.status is ApprovalStatus.APPROVED
    assert reloaded.content == edited
    assert reloaded.resolved_at is not None
    assert [d.reviewer.user_id for d in reloaded.decisions] == [bd.user_id]
    engine.assert_approved(request.id, edited)
    assert engine.pending_for(Role.BD_MANAGER) == []

    with connect() as conn:
        (audit_rows,) = conn.execute(
            "SELECT count(*) FROM audit_log WHERE entity_id = %s", (request.id,)
        ).fetchone()
    assert audit_rows == 1


def test_concurrent_decisions_are_refused(engine, connect):
    from recruiting.approvals.postgres import PostgresApprovalStore

    store = PostgresApprovalStore(connect)
    bd_1 = make_user(connect, Role.BD_MANAGER)
    bd_2 = make_user(connect, Role.BD_MANAGER)
    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "contact-1", DRAFT, requested_by="agent-3")

    # Two reviewers both loaded the request while it had no decisions.
    stale = store.get(request.id)
    engine.decide(request.id, bd_1, DecisionAction.APPROVE)
    late = Decision(bd_2, DecisionAction.APPROVE, stale.content_sha256, None, stale.created_at)
    with pytest.raises(ConcurrentDecision):
        store.record_decision(stale, late, expected_prior_decisions=0)


def test_unknown_or_malformed_ids(engine):
    with pytest.raises(UnknownApprovalRequest):
        engine.get(str(uuid.uuid4()))
    with pytest.raises(UnknownApprovalRequest):
        engine.get("not-a-uuid")


def test_opt_out_store(connect):
    from recruiting.compliance import OptOutRegistry
    from recruiting.compliance.postgres import PostgresOptOutStore

    registry = OptOutRegistry(PostgresOptOutStore(connect))
    first = registry.record("Lee@Example.com", source="unsubscribe-link")
    again = registry.record("lee@example.com", source="reply")
    assert again == first
    assert registry.is_opted_out("LEE@example.com")
    assert not registry.is_opted_out("someone@example.com")


def test_user_directory(connect):
    from recruiting.identity.users import PostgresUserDirectory

    reviewer = make_user(connect, Role.RECRUITER)
    with connect() as conn:
        (email,) = conn.execute(
            "SELECT email FROM app_user WHERE id = %s", (reviewer.user_id,)
        ).fetchone()
    user = PostgresUserDirectory(connect).by_email(email.upper())
    assert user is not None
    assert user.role is Role.RECRUITER
    assert PostgresUserDirectory(connect).by_email("nobody@cbs.example") is None
