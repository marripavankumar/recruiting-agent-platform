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


def test_company_job_and_usage_repositories(connect):
    from datetime import UTC, date, datetime

    from recruiting.ai.llm import UsageRecord
    from recruiting.records.memory import new_id
    from recruiting.records.models import (
        Company,
        ConsentBasis,
        Contact,
        ContactRole,
        Job,
        JobPosting,
        OutreachStatus,
    )
    from recruiting.records.postgres import PostgresCompanies, PostgresJobs, PostgresUsageLedger

    with connect() as conn:
        # DELETE, not TRUNCATE ... CASCADE: that would cascade through app_user to gate_policy.
        for table in ("job_posting", "job", "company_contact", "company", "llm_usage"):
            conn.execute(f"DELETE FROM {table}")
    companies = PostgresCompanies(connect)
    company = companies.save(
        Company(new_id(), "Northfield Axle", "https://www.Northfield-Axle.example/", "list",
                industry="Automotive", revenue_usd=1.2e9, country="US", locations=["Troy, MI"],
                priority_score=80)
    )  # fmt: skip
    assert companies.by_domain("northfield-axle.example").name == "Northfield Axle"
    assert companies.get(company.id).sources == ["list"]  # filled from source when empty
    company.sources, company.jd_promised_at = ["list", "trade-db"], datetime.now(UTC)  # S3, S9
    companies.save(company)
    loaded = companies.get(company.id)
    assert loaded.sources == ["list", "trade-db"] and loaded.jd_promised_at is not None
    assert [c.id for c in companies.list(min_priority=70)] == [company.id]
    companies.set_outreach_status(company.id, OutreachStatus.CONTACTED)
    assert companies.get(company.id).outreach_status is OutreachStatus.CONTACTED

    contact = Contact(new_id(), company.id, "Rachel Lee", "Engineering Manager",
                      ContactRole.ENGINEERING_MANAGER, email="R.Lee@northfield-axle.example",
                      country="US", consent_basis=ConsentBasis.NOT_REQUIRED)  # fmt: skip
    companies.save_contact(contact)
    assert companies.contact_by_email("r.lee@NORTHFIELD-axle.example").id == contact.id
    assert companies.contact_by_email("r.lee@northfield-axle.example").email_valid is True
    contact.email_valid = False  # S6: hard bounce
    companies.save_contact(contact)
    assert companies.contact_by_email("r.lee@northfield-axle.example").email_valid is False

    posting = JobPosting(new_id(), company.id, "CAE Engineer", "CAE Engineer",
                         "https://northfield-axle.example/j/1", date_posted=date(2026, 11, 20))  # fmt: skip
    companies.save_posting(posting)
    posting.active = False
    companies.save_posting(posting)
    assert companies.postings(company.id) == []
    assert companies.posting_by_url(posting.url).active is False

    jobs = PostgresJobs(connect)
    code = jobs.next_job_code(2026)
    job = Job(new_id(), code, company.id, "CAE Engineer", required_skills=["LS-DYNA"],
              review_fields=["visa_requirements"], analysis={"must_have_skills": ["LS-DYNA"]})  # fmt: skip
    jobs.add(job)
    assert jobs.next_job_code(2026) != code
    job.review_fields = []
    jobs.save(job)
    loaded = jobs.get(job.id)
    assert loaded.review_fields == [] and loaded.analysis["must_have_skills"] == ["LS-DYNA"]
    assert [j.id for j in jobs.list(client_company_id=company.id)] == [job.id]
    assert jobs.list(client_company_id="not-a-uuid") == []

    ledger = PostgresUsageLedger(connect)
    ledger.record(
        UsageRecord(
            "jd_analysis@1", "claude", "claude-opus-5-5", 1000, 200, 0.008, datetime.now(UTC)
        )
    )
    assert ledger.entries()[-1].template_key == "jd_analysis@1"


def test_delivery_log_tracking_and_cap_counts(engine, connect):
    from datetime import UTC, datetime, timedelta

    from recruiting.domain import Channel
    from recruiting.outreach import AlreadySent, DeliveryResult, DeliveryStatus, OutboundMessage
    from recruiting.outreach.postgres import PostgresDeliveryLog

    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "contact-1", DRAFT, requested_by="agent-3")
    log = PostgresDeliveryLog(connect)
    now = datetime.now(UTC)
    log.claim(OutboundMessage(request.id, Channel.EMAIL, "Lee@Example.com", DRAFT), now)
    log.complete(request.id, DeliveryResult(DeliveryStatus.SENT, "m-1"), now)
    assert log.sent_count_since(Channel.EMAIL, now - timedelta(hours=1)) >= 1
    assert log.latest_to("lee@example.com").message.approval_request_id == request.id
    log.record_reply(request.id, now)
    log.record_reply(request.id, now + timedelta(hours=1))  # first reply time is kept
    assert log.get(request.id).replied_at == now
    bounced = log.record_bounce(request.id, "550 5.1.1 user unknown")  # S6
    assert bounced.status is DeliveryStatus.BOUNCED and bounced.provider_message_id == "m-1"
    assert bounced.error == "550 5.1.1 user unknown"

    # S24: a retry is scheduled under the same approval and claimed again exactly once.
    retry = engine.submit(GateType.FIRST_CLIENT_EMAIL, "contact-2", DRAFT, requested_by="agent-3")
    message = OutboundMessage(retry.id, Channel.EMAIL, "kim@example.com", DRAFT)
    log.claim(message, now)
    waiting = log.defer(message, "HTTP 429", now + timedelta(minutes=1), now,
                        claimed=True, count_attempt=True)  # fmt: skip
    assert waiting.status is DeliveryStatus.RETRY_SCHEDULED and waiting.attempts == 1
    assert retry.id not in [r.message.approval_request_id for r in log.due_retries(now)]
    later = now + timedelta(minutes=2)
    assert retry.id in [r.message.approval_request_id for r in log.due_retries(later)]
    log.claim(message, later)
    with pytest.raises(AlreadySent):
        log.claim(message, later)  # a concurrent retry cannot send it twice
    with pytest.raises(AlreadySent):
        log.defer(message, "cap", later, later, claimed=False, count_attempt=False)
    log.complete(retry.id, DeliveryResult(DeliveryStatus.SENT, "m-2"), later)
    assert log.get(retry.id).attempts == 1 and log.get(retry.id).status is DeliveryStatus.SENT
    capped = engine.submit(GateType.FIRST_CLIENT_EMAIL, "contact-3", DRAFT, requested_by="agent-3")
    deferred = log.defer(OutboundMessage(capped.id, Channel.EMAIL, "x@example.com", DRAFT),
                         "Daily cap reached", later, now, claimed=False, count_attempt=False)  # fmt: skip
    assert deferred.status is DeliveryStatus.RETRY_SCHEDULED and deferred.attempts == 0


def test_candidates_rankings_and_weights(connect):
    from datetime import UTC, datetime

    from recruiting.agents.ranking import RankingWeights
    from recruiting.records.memory import new_id
    from recruiting.records.models import (
        Candidate,
        Company,
        Job,
        RankedCandidate,
        Ranking,
        Verdict,
    )
    from recruiting.records.postgres import (
        PostgresCandidates,
        PostgresCompanies,
        PostgresJobs,
        PostgresRankings,
        PostgresWeights,
    )

    with connect() as conn:
        for table in ("candidate_ranking", "candidate", "ranking_settings"):
            conn.execute(f"DELETE FROM {table}")
    candidates = PostgresCandidates(connect)
    priya = Candidate(new_id(), "Priya", "ats", email="Priya@Mail.example", phone="+1 (313) 555-0101",
                      linkedin_url="https://www.linkedin.com/in/priya-cae/", skills=["LS-DYNA"],
                      industries=["Automotive"], work_history=[{"title": "CAE Engineer", "end_year": None}],
                      communication={"recruiter_rating": 5})  # fmt: skip
    candidates.save(priya)
    loaded = candidates.get(priya.id)
    assert loaded.industries == ["Automotive"] and loaded.communication == {"recruiter_rating": 5}
    assert candidates.find_duplicate("priya@mail.EXAMPLE", None, None).id == priya.id
    assert candidates.find_duplicate(None, "3135550101", None).id == priya.id
    assert candidates.find_duplicate(None, None, "priya-cae").id == priya.id
    assert candidates.find_duplicate("x@y.example", None, None) is None

    company = PostgresCompanies(connect).save(
        Company(new_id(), "Rank Co", f"{new_id()}.example", "test")
    )
    job = PostgresJobs(connect).add(
        Job(new_id(), f"JOB-2099-{new_id()[:4]}", company.id, "CAE Engineer")
    )
    rankings = PostgresRankings(connect)
    entry = RankedCandidate(priya.id, 91, {"skill_match": 1.0}, ["Has LS-DYNA"], [], rank=1)
    rankings.save(Ranking(job.id, datetime.now(UTC), {"skill_match": 0.25}, [entry], searched=1))
    rankings.review(job.id, priya.id, Verdict.QUALIFIED, "strong", "u-rec")
    rerun = rankings.save(Ranking(job.id, datetime.now(UTC), {"skill_match": 0.25},
                                  [RankedCandidate(priya.id, 93, {}, [], [], rank=1)], searched=1))  # fmt: skip
    assert rerun.candidates[0].verdict is Verdict.QUALIFIED
    assert rankings.get(job.id).qualified_in_top_20 == 1
    assert rankings.get(job.id).widened is None
    widened = {"title_variants": ["FEA Engineer"], "location": "state", "by": "u-rec"}  # S12
    rankings.save(Ranking(job.id, datetime.now(UTC), {"skill_match": 0.25},
                          [RankedCandidate(priya.id, 93, {}, [], [], rank=1)], 1, widened))  # fmt: skip
    assert rankings.get(job.id).widened == widened

    weights = PostgresWeights(connect)
    assert weights.get() == RankingWeights()
    weights.save(RankingWeights(location=0.2), "u-admin")
    assert weights.get().location == 0.2


def test_engagement_store(connect):
    from datetime import UTC, datetime

    from recruiting.records.memory import new_id
    from recruiting.records.models import Candidate, Company, Engagement, EngagementStatus, Job
    from recruiting.records.postgres import (
        PostgresCandidates,
        PostgresCompanies,
        PostgresEngagements,
        PostgresJobs,
    )

    company = PostgresCompanies(connect).save(
        Company(new_id(), "Eng Co", f"{new_id()}.example", "test")
    )
    job = PostgresJobs(connect).add(
        Job(new_id(), f"JOB-2098-{new_id()[:4]}", company.id, "CAE Engineer")
    )
    candidate = PostgresCandidates(connect).save(Candidate(new_id(), "John", "ats"))
    store = PostgresEngagements(connect)
    approval_id = new_id()
    engagement = Engagement(new_id(), job.id, candidate.id, approvals={"linkedin": approval_id},
                            token_hash="a" * 64, missing=["resume", "consent"])  # fmt: skip
    engagement.log(datetime.now(UTC), "drafted")
    store.save(engagement)
    assert store.find(job.id, candidate.id).approvals == {"linkedin": approval_id}
    assert store.by_approval(approval_id).id == engagement.id
    assert store.by_token_hash("a" * 64).missing == ["resume", "consent"]
    engagement.status = EngagementStatus.COMPLETE
    store.save(engagement)
    assert [e.id for e in store.list(job_id=job.id, status=EngagementStatus.COMPLETE)] == [
        engagement.id
    ]
    assert store.for_candidate(candidate.id)[0].history[0]["event"] == "drafted"


def test_submission_store(connect):
    from datetime import UTC, datetime

    from recruiting.approvals import ApprovalEngine
    from recruiting.approvals.postgres import PostgresApprovalStore
    from recruiting.domain import GateType
    from recruiting.records.memory import new_id
    from recruiting.records.models import (
        Candidate,
        ClientDecision,
        Company,
        Job,
        Submission,
        SubmissionStatus,
    )
    from recruiting.records.postgres import (
        PostgresCandidates,
        PostgresCompanies,
        PostgresJobs,
        PostgresSubmissions,
    )

    company = PostgresCompanies(connect).save(
        Company(new_id(), "Sub Co", f"{new_id()}.example", "test")
    )
    job = PostgresJobs(connect).add(
        Job(new_id(), f"JOB-2097-{new_id()[:4]}", company.id, "CAE Engineer")
    )
    candidate = PostgresCandidates(connect).save(Candidate(new_id(), "Priya", "ats"))
    store = PostgresSubmissions(connect)
    sub = Submission(new_id(), job.id, candidate.id, company.id,
                     match={"score": 88, "flags": []}, package={"match_percent": 88},
                     warnings=["Acknowledged: x"])  # fmt: skip
    sub.log(datetime.now(UTC), "matched")
    store.save(sub)
    assert store.get(sub.id).match["score"] == 88

    approval = ApprovalEngine(store=PostgresApprovalStore(connect)).submit(
        GateType.RESUME_SUBMISSION, sub.id, {"subject": "s", "body": "b"}, "agent-11"
    )
    now = datetime.now(UTC)
    sub.approval_request_id, sub.right_to_represent_at = approval.id, now
    sub.status, sub.sent_at, sub.sent_content = SubmissionStatus.SUBMITTED, now, {"body": "b"}
    sub.client_decision = ClientDecision.INTERVIEW
    store.save(sub)
    loaded = store.by_approval(approval.id)
    assert loaded.status is SubmissionStatus.SUBMITTED
    assert loaded.client_decision is ClientDecision.INTERVIEW
    assert loaded.warnings == ["Acknowledged: x"] and loaded.history[0]["event"] == "matched"
    listed = store.list(candidate_id=candidate.id, client_company_id=company.id)
    assert [s.id for s in listed] == [sub.id]
    assert store.list(job_id="not-a-uuid") == []


def test_follow_up_store_and_cadence(connect):
    from datetime import UTC, date, datetime, timedelta

    from recruiting.records.memory import new_id
    from recruiting.records.models import (
        Candidate,
        Company,
        FollowUp,
        FollowUpKind,
        FollowUpStatus,
        Job,
        PlacementStage,
        Submission,
    )
    from recruiting.records.postgres import (
        PostgresCadence,
        PostgresCandidates,
        PostgresCompanies,
        PostgresFollowUps,
        PostgresJobs,
        PostgresSubmissions,
    )

    with connect() as conn:
        conn.execute("DELETE FROM follow_up_cadence")
    company = PostgresCompanies(connect).save(
        Company(new_id(), "FU Co", f"{new_id()}.example", "test")
    )
    job = PostgresJobs(connect).add(
        Job(new_id(), f"JOB-2096-{new_id()[:4]}", company.id, "CAE Engineer")
    )
    candidate = PostgresCandidates(connect).save(Candidate(new_id(), "Wei", "ats"))
    now = datetime.now(UTC)
    sub = Submission(new_id(), job.id, candidate.id, company.id,
                     stage=PlacementStage.OFFER_ACCEPTED, stage_at=now,
                     start_date=date(2027, 1, 4))  # fmt: skip
    subs = PostgresSubmissions(connect)
    subs.save(sub)
    loaded = subs.get(sub.id)
    assert loaded.stage is PlacementStage.OFFER_ACCEPTED and loaded.start_date == date(2027, 1, 4)

    store = PostgresFollowUps(connect)
    item = FollowUp(new_id(), FollowUpKind.START_DATE, sub.id, "recruiter", now,
                    now + timedelta(days=3), job_id=job.id, candidate_id=candidate.id,
                    context={"recipient": "x@y.example"}, approvals=["a1"])  # fmt: skip
    item.log(now, "created")
    store.save(item)
    found = store.find(FollowUpKind.START_DATE, sub.id)
    assert found.id == item.id and found.context == {"recipient": "x@y.example"}
    assert found.approvals == ["a1"] and found.history[0]["event"] == "created"
    item.status, item.due_at = FollowUpStatus.DONE, None
    store.save(item)
    assert store.list(status=FollowUpStatus.OPEN, job_id=job.id) == []
    assert [i.id for i in store.list(job_id=job.id)] == [item.id]
    short = FollowUp(new_id(), FollowUpKind.CANDIDATE_SHORTFALL, job.id, "recruiter", now, now,
                     job_id=job.id)  # fmt: skip
    store.save(short)
    assert store.find(FollowUpKind.CANDIDATE_SHORTFALL, job.id).id == short.id
    promised = FollowUp(new_id(), FollowUpKind.JD_PROMISED, f"co-{new_id()}", "bd_manager", now,
                        now + timedelta(days=3))  # fmt: skip
    store.save(promised)  # S9 kind, migration 0010
    assert store.find(FollowUpKind.JD_PROMISED, promised.subject_id).id == promised.id

    cadence = PostgresCadence(connect)
    assert cadence.get()[FollowUpKind.CLIENT_OUTREACH] == [3, 7, 14]
    cadence.save(FollowUpKind.CLIENT_OUTREACH, [2, 5], "u-admin")
    assert cadence.get()[FollowUpKind.CLIENT_OUTREACH] == [2, 5]


def test_scheduler_job_claims_are_shared(connect):
    from datetime import UTC, datetime, timedelta

    from recruiting.scheduler import PostgresJobState

    with connect() as conn:
        conn.execute("DELETE FROM scheduler_job")
    first, second = PostgresJobState(connect), PostgresJobState(connect)
    now = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
    hour = timedelta(hours=1)
    assert first.claim("follow_ups", hour, now) is True
    assert second.claim("follow_ups", hour, now) is False  # another replica: not twice
    first.finish("follow_ups", now, False, "RuntimeError: boom", {})
    assert second.claim("follow_ups", hour, now + timedelta(minutes=4)) is False
    assert second.claim("follow_ups", hour, now + timedelta(minutes=5)) is True  # failed: sooner
    second.finish("follow_ups", now + timedelta(minutes=6), True, None, {"created": 2})
    [state] = first.all()
    assert state.last_status == "ok" and state.last_result == {"created": 2}
    assert state.interval_seconds == 3600


def test_inbound_messages_are_claimed_once(connect):
    from datetime import UTC, datetime, timedelta

    from recruiting.mailbox import MAX_ATTEMPTS, PostgresProcessed

    with connect() as conn:
        conn.execute("DELETE FROM inbound_message")
    first, second = PostgresProcessed(connect), PostgresProcessed(connect)
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    assert first.claim("<m-1@x>", now) == 1
    assert second.claim("<m-1@x>", now) == 0  # another replica is handling it
    first.finish("<m-1@x>", now, True, "agent-4:jd_received", None)
    assert second.claim("<m-1@x>", now + timedelta(hours=1)) == 0  # done: never again

    assert first.claim("<m-2@x>", now) == 1
    first.finish("<m-2@x>", now, False, "failed", "RuntimeError: boom")
    assert second.claim("<m-2@x>", now) == 2  # failed: tried again
    second.finish("<m-2@x>", now, False, "failed", "RuntimeError: boom")
    assert first.claim("<m-2@x>", now) == MAX_ATTEMPTS
    first.finish("<m-2@x>", now, False, "failed", "RuntimeError: boom")
    assert first.claim("<m-2@x>", now) == 0  # out of attempts
    assert first.failures_since(now - timedelta(minutes=1)) == 1

    assert first.claim("<m-3@x>", now) == 1  # a worker crashes while handling it
    assert second.claim("<m-3@x>", now + timedelta(minutes=5)) == 0
    assert second.claim("<m-3@x>", now + timedelta(minutes=16)) == 2  # stuck: taken over
