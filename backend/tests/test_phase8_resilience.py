"""Phase 8 exception paths: S3 (two sources), S4 (scheduled re-check), S9 (out-of-office and
"JD to follow" replies) and S24 (external API failures: retry with back-off, no duplicates)."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

pytest.importorskip("langgraph")

from test_phase8_end_to_end import (  # noqa: E402
    ADMIN,
    BD,
    CAREERS,
    CSV,
    HOOK,
    JD,
    approve,
    build,
    h,
    ok,
)

from recruiting.agents.candidate_search import CandidateSearchAgent  # noqa: E402
from recruiting.agents.discovery import (  # noqa: E402
    CompanyDiscoveryAgent,
    CsvCompanySource,
    DiscoveryCriteria,
)
from recruiting.approvals import ApprovalEngine, DecisionAction, Reviewer  # noqa: E402
from recruiting.compliance import OptOutRegistry  # noqa: E402
from recruiting.connectors.retry import call_with_retry  # noqa: E402
from recruiting.domain import Channel, GateType, Role  # noqa: E402
from recruiting.outreach import (  # noqa: E402
    DeliveryDeferred,
    DeliveryResult,
    DeliveryStatus,
    OutboundMessage,
    OutreachService,
    TransientDeliveryError,
)
from recruiting.outreach.channels import GmailEmailChannel  # noqa: E402
from recruiting.records.memory import InMemoryCompanies  # noqa: E402
from recruiting.records.models import Job  # noqa: E402

RACHEL = "r.lee@northfield-axle.example"


@pytest.fixture
def ctx():
    return build()


def kinds(client, user):
    return {i["kind"] for i in ok(client.get("/follow-ups", headers=h(user)))}


def first_email(client):
    ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV}, headers=h(BD)))
    ok(client.post("/companies/hiring-signals", json={}, headers=h(BD)))
    [first] = ok(client.post("/companies/outreach-drafts", json={}, headers=h(BD)))["drafted"]
    return first


# ----- S3 ----------


def test_s3_same_company_from_two_sources_is_one_record_with_both_sources(ctx):
    client, _, _, _ = ctx
    ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV}, headers=h(BD)))
    again = ok(client.post("/companies/import", json={"source_name": "trade-db", "csv": CSV},
                           headers=h(BD)))  # fmt: skip
    assert again["created"] == 0 and again["merged"] == 2
    companies = ok(client.get("/companies", headers=h(BD)))
    assert len(companies) == 2
    assert {tuple(c["sources"]) for c in companies} == {("list", "trade-db")}
    assert {c["source"] for c in companies} == {"list"}  # first source stays the origin
    # The same source again is an update, not a merge.
    third = ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV},
                           headers=h(BD)))  # fmt: skip
    assert third["merged"] == 0


# ----- S4 ----------


class SwitchableFetcher:
    def __init__(self):
        self.pages = {"northfield": CAREERS}
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        return next((page for key, page in self.pages.items() if key in url), "<html></html>")


QUIET_CSV = CSV + (
    'Quiet Gears,quiet-gears.example,Automotive,900M,US,"Flint, MI",'
    "https://quiet-gears.example/careers,Ann Ruiz,HR Manager,ann@quiet-gears.example\n"
)


def test_s4_company_without_jobs_stays_low_priority_and_is_rechecked_on_schedule(ctx):
    client, services, _, clock = ctx
    fetcher = SwitchableFetcher()
    services.platform.signals._fetcher = fetcher
    ok(client.post("/companies/import", json={"source_name": "list", "csv": QUIET_CSV},
                   headers=h(BD)))  # fmt: skip
    first = ok(client.post("/companies/hiring-signals/run-due", headers=h(BD)))
    assert first["companies_checked"] == 2 and first["not_due"] == 0
    quiet = next(
        c for c in ok(client.get("/companies", headers=h(BD))) if c["name"] == "Quiet Gears"
    )
    assert quiet["priority_score"] == 0  # kept in the Company DB, low priority

    assert ok(client.post("/companies/hiring-signals/run-due", headers=h(BD)))["not_due"] == 2
    clock.offset = timedelta(days=8)  # hiring companies weekly, quiet ones every two weeks
    week = ok(client.post("/companies/hiring-signals/run-due", headers=h(BD)))
    assert week["companies_checked"] == 1 and week["not_due"] == 1

    # Quiet Gears starts hiring; the fortnightly check picks it up and raises its priority.
    clock.offset = timedelta(days=15)
    posted = clock.now.date().isoformat()
    fetcher.pages["quiet-gears"] = (
        '<script type="application/ld+json">[{"@type":"JobPosting","title":"Quality Engineer",'
        f'"url":"https://quiet-gears.example/j/1","datePosted":"{posted}"}}]</script>'
    )
    later = ok(client.post("/companies/hiring-signals/run-due", headers=h(BD)))
    assert later["scores"][quiet["id"]] > 0


# ----- S9 ----------


def test_s9_out_of_office_is_not_a_reply_and_the_reminder_stays(ctx):
    client, services, _, clock = ctx
    first = first_email(client)
    approve(client, first["approval_request_id"], BD)
    ooo = {"from_address": RACHEL, "subject": "Automatic reply: Engineering talent",
           "body": "I am out of the office until Monday.", "message_id": "<ooo@mail>"}  # fmt: skip
    result = ok(client.post("/inbound/email", json=ooo, headers=HOOK))
    assert result["category"] == "out_of_office" and result["job_ids"] == []
    receipt = services.platform.deliveries.get(first["approval_request_id"])
    assert receipt.replied_at is None
    ok(client.post("/follow-ups/run", headers=h(ADMIN)))
    assert "client_outreach" in kinds(client, BD)  # the S8 reminder is still set
    company = services.platform.companies.get(first["company_id"])
    assert company.outreach_status == "contacted"


def test_s9_jd_to_follow_sets_a_reminder_until_the_jd_arrives(ctx):
    client, services, _, _ = ctx
    first = first_email(client)
    approve(client, first["approval_request_id"], BD)
    promise = {"from_address": RACHEL, "subject": "Re: Engineering talent",
               "body": "Thanks, I will send the JD next week.", "message_id": "<p@mail>"}  # fmt: skip
    result = ok(client.post("/inbound/email", json=promise, headers=HOOK))
    assert result["category"] == "jd_to_follow" and result["job_ids"] == []
    ok(client.post("/follow-ups/run", headers=h(ADMIN)))
    [task] = [i for i in ok(client.get("/follow-ups", headers=h(BD))) if i["kind"] == "jd_promised"]
    assert task["company_id"] == first["company_id"] and "job description" in task["note"]
    assert "client_outreach" not in kinds(client, BD)  # a real reply: no more cold chasing

    jd = {"from_address": RACHEL, "subject": "Re: Engineering talent", "body": "Here it is.",
          "attachments_text": JD, "message_id": "<jd@mail>"}  # fmt: skip
    assert len(ok(client.post("/inbound/email", json=jd, headers=HOOK))["job_ids"]) == 1
    ok(client.post("/follow-ups/run", headers=h(ADMIN)))
    assert "jd_promised" not in kinds(client, BD)
    assert services.platform.companies.get(first["company_id"]).jd_promised_at is None


# ----- S24: sending ----------


def flaky(email, failures):
    """The provider refuses the first `failures` sends (rate limited), then works."""
    real = email.deliver
    left = [failures]

    def deliver(message):
        if left[0]:
            left[0] -= 1
            raise TransientDeliveryError("HTTP 429 from the provider")
        return real(message)

    email.deliver = deliver


def test_s24_send_is_retried_with_back_off_and_never_duplicated(ctx):
    client, services, email, clock = ctx
    flaky(email, 2)
    first = first_email(client)
    approval_id = first["approval_request_id"]
    approve(client, approval_id, BD)
    receipt = services.platform.deliveries.get(approval_id)
    assert receipt.status == "retry_scheduled" and receipt.attempts == 1 and email.sent == []

    # Paused and the user alerted; the operations view shows it.
    ops = ok(client.get("/admin/operations", headers=h(ADMIN)))
    assert "1 message is waiting to retry delivery (S24)" in ops["alerts"]
    assert ops["email_30d"]["retry_scheduled"] == 1 and ops["email_30d"]["sent"] == 0

    retry = "/admin/outreach/retry"
    assert ok(client.post(retry, headers=h(ADMIN)))["due"] == 0  # back-off not over yet
    assert client.post(retry, headers=h(BD)).status_code == 403
    clock.offset = timedelta(minutes=2)
    assert ok(client.post(retry, headers=h(ADMIN)))["deferred"] == 1  # second refusal
    receipt = services.platform.deliveries.get(approval_id)
    assert receipt.attempts == 2
    assert receipt.next_attempt_at - clock.now > timedelta(minutes=3)  # 4-minute back-off
    clock.offset = timedelta(minutes=7)
    assert ok(client.post(retry, headers=h(ADMIN)))["sent"] == 1
    assert ok(client.post(retry, headers=h(ADMIN)))["due"] == 0
    assert [m.recipient for m in email.sent] == [RACHEL]  # exactly once
    assert services.platform.deliveries.get(approval_id).status == "sent"
    ok(client.post("/follow-ups/run", headers=h(ADMIN)))
    assert "client_outreach" in kinds(client, BD)  # the normal flow carries on


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


class Refusing:
    channel = Channel.EMAIL

    def __init__(self, error):
        self.error, self.calls = error, 0

    def deliver(self, message):
        self.calls += 1
        if self.error:
            raise self.error
        return DeliveryResult(DeliveryStatus.SENT, "m-1")


def approved(engine, body="hi"):
    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "s", {"body": body}, requested_by="a")
    engine.decide(request.id, Reviewer("u-bd", Role.BD_MANAGER), DecisionAction.APPROVE)
    return OutboundMessage(request.id, Channel.EMAIL, "lee@x.example", {"body": body})


def test_s24_gives_up_after_max_attempts_and_fails():
    engine, clock = ApprovalEngine(), Clock()
    adapter = Refusing(TransientDeliveryError("connection refused"))
    service = OutreachService(engine, OptOutRegistry(), [adapter], clock=clock, max_attempts=3)
    message = approved(engine)
    with pytest.raises(DeliveryDeferred):
        service.send(message)
    for _ in range(2):
        clock.now += timedelta(hours=7)
        service.retry_due()
    receipt = service.receipt(message.approval_request_id)
    assert receipt.status is DeliveryStatus.FAILED and "Gave up after 3 attempts" in receipt.error
    assert adapter.calls == 3
    clock.now += timedelta(days=1)
    assert service.retry_due().due == 0


def test_s24_ambiguous_errors_are_not_retried():
    engine = ApprovalEngine()
    adapter = Refusing(httpx.ReadTimeout("no answer"))  # the provider may have sent it
    service = OutreachService(engine, OptOutRegistry(), [adapter])
    message = approved(engine)
    with pytest.raises(httpx.ReadTimeout):
        service.send(message)
    assert service.receipt(message.approval_request_id).status is DeliveryStatus.FAILED


def test_s24_opt_out_while_waiting_stops_the_retry():
    engine, clock, opt_outs = ApprovalEngine(), Clock(), OptOutRegistry()
    adapter = Refusing(TransientDeliveryError("HTTP 503 from the provider", retry_after=120))
    service = OutreachService(engine, opt_outs, [adapter], clock=clock)
    message = approved(engine)
    with pytest.raises(DeliveryDeferred):
        service.send(message)
    waiting = service.receipt(message.approval_request_id)
    assert waiting.next_attempt_at == clock.now + timedelta(minutes=2)  # Retry-After honoured
    opt_outs.record("lee@x.example", source="reply")
    clock.now += timedelta(minutes=3)
    assert service.retry_due().failed == 1
    receipt = service.receipt(message.approval_request_id)
    assert receipt.status is DeliveryStatus.FAILED and "RecipientOptedOut" in receipt.error
    assert adapter.calls == 1


def test_s24_email_adapter_classifies_provider_errors():
    def channel(status=None, exc=None, headers=None):
        def handler(request):
            if exc:
                raise exc
            return httpx.Response(status, headers=headers or {}, json={"id": "g-1"})

        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://g.test")
        return GmailEmailChannel("bd@cbs.example", lambda: "token", client=http)

    message = OutboundMessage("a-1", Channel.EMAIL, "lee@x.example", {"subject": "s", "body": "b"})
    with pytest.raises(TransientDeliveryError) as limited:
        channel(429, headers={"Retry-After": "30"}).deliver(message)
    assert limited.value.retry_after == 30
    with pytest.raises(TransientDeliveryError):
        channel(503).deliver(message)
    with pytest.raises(TransientDeliveryError):
        channel(exc=httpx.ConnectError("refused")).deliver(message)
    with pytest.raises(httpx.HTTPStatusError):
        channel(500).deliver(message)  # may have been sent: not retried
    assert channel(200).deliver(message).provider_message_id == "g-1"


# ----- S24: searching external sources ----------


def test_s24_read_calls_retry_with_back_off():
    pauses, calls = [], []

    def flaky_read():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("refused")
        return "ok"

    assert call_with_retry(flaky_read, sleep=pauses.append) == "ok"
    assert pauses == [0.5, 1.0]
    with pytest.raises(ValueError):
        call_with_retry(lambda: (_ for _ in ()).throw(ValueError("bad")), sleep=pauses.append)
    assert pauses == [0.5, 1.0]  # not transient: no retry


class DownSource:
    name = "board-down"

    def __init__(self):
        self.calls = 0

    def search(self, *args, **kwargs):
        self.calls += 1
        raise httpx.ConnectError("board unreachable")


def test_s24_a_failing_source_does_not_stop_the_others(ctx):
    _, services, _, _ = ctx
    platform = services.platform
    down = DownSource()
    agent = CandidateSearchAgent(platform.candidates, platform.search, platform.resume_intake,
                                 platform.candidate_search._ranking, [down],
                                 sleep=lambda _s: None)  # fmt: skip
    job = Job("j-1", "JOB-1", "c-1", "Quality Engineer", required_skills=["PFMEA"])
    ranking, report = agent.run(job)
    assert down.calls == 3 and "board unreachable" in report.external_errors["board-down"]
    assert ranking.job_id == "j-1"

    companies = InMemoryCompanies()
    discovery = CompanyDiscoveryAgent(companies, [down, CsvCompanySource("list", CSV)],
                                      sleep=lambda _s: None)  # fmt: skip
    result = discovery.run(DiscoveryCriteria())
    assert "board-down" in result.source_errors and result.created == 2
