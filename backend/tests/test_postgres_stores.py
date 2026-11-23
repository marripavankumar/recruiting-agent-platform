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
    from recruiting.identity.postgres import PostgresUserDirectory

    reviewer = make_user(connect, Role.RECRUITER)
    with connect() as conn:
        (email,) = conn.execute(
            "SELECT email FROM app_user WHERE id = %s", (reviewer.user_id,)
        ).fetchone()
    user = PostgresUserDirectory(connect).by_email(email.upper())
    assert user is not None
    assert user.role is Role.RECRUITER
    assert PostgresUserDirectory(connect).by_email("nobody@cbs.example") is None


def test_delivery_log_claims_each_approval_once(engine, connect):
    from datetime import UTC, datetime

    from recruiting.domain import Channel
    from recruiting.outreach import AlreadySent, DeliveryResult, DeliveryStatus, OutboundMessage
    from recruiting.outreach.postgres import PostgresDeliveryLog

    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "contact-1", DRAFT, requested_by="agent-3")
    log = PostgresDeliveryLog(connect)
    message = OutboundMessage(request.id, Channel.EMAIL, "lee@example.com", DRAFT)
    now = datetime.now(UTC)

    log.claim(message, now)
    assert log.get(request.id).status is DeliveryStatus.SENDING
    with pytest.raises(AlreadySent):
        log.claim(message, now)  # a second claimer (retry or concurrent send) is refused

    receipt = log.complete(request.id, DeliveryResult(DeliveryStatus.SENT, "msg-1"), now)
    assert receipt.status is DeliveryStatus.SENT
    assert receipt.provider_message_id == "msg-1"
    assert receipt.message == message
    assert log.get(str(uuid.uuid4())) is None


def test_policy_change_reaches_other_engines_without_restart(connect):
    from recruiting.approvals import GatePolicy
    from recruiting.approvals.postgres import PostgresApprovalStore, PostgresPolicyStore

    admin = make_user(connect, Role.ADMINISTRATOR)
    instance_a = ApprovalEngine(PostgresPolicyStore(connect), store=PostgresApprovalStore(connect))
    instance_b = ApprovalEngine(PostgresPolicyStore(connect), store=PostgresApprovalStore(connect))
    gate = GateType.RESUME_SUBMISSION
    changed = GatePolicy(gate, Role.RECRUITER, Role.ACCOUNT_MANAGER)
    try:
        instance_a.update_policy(changed, admin.user_id)
        assert instance_b.policy_for(gate) == changed
    finally:
        PostgresPolicyStore(connect).save(DEFAULT_POLICIES[gate], admin.user_id)


def test_user_admin(connect):
    from recruiting.identity import DuplicateUser, InvalidUser, UnknownUser
    from recruiting.identity.postgres import PostgresUserDirectory

    admin = make_user(connect, Role.ADMINISTRATOR)
    users = PostgresUserDirectory(connect)
    email = f"{uuid.uuid4()}@cbs.example"
    created = users.create_user(email, "New Recruiter", Role.RECRUITER, None, admin.user_id)
    assert users.by_email(email).user_id == created.user_id
    with pytest.raises(DuplicateUser):
        users.create_user(email.upper(), "Again", Role.RECRUITER, None, admin.user_id)
    with pytest.raises(InvalidUser):
        users.create_user("hm@client.example", "HM", Role.CLIENT, str(uuid.uuid4()), admin.user_id)

    updated = users.update_user(created.user_id, admin.user_id, role=Role.BD_MANAGER, active=False)
    assert (updated.role, updated.active) == (Role.BD_MANAGER, False)
    assert users.by_email(email) is None  # inactive users cannot sign in
    assert created.user_id in [u.user_id for u in users.list_users()]
    with pytest.raises(UnknownUser):
        users.update_user(str(uuid.uuid4()), admin.user_id, active=False)
