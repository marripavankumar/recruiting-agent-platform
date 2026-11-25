"""Daily reminder digest (Agent 12): each staff user gets one email a day with their overdue and
due-today follow-ups, at the configured local hour, never twice, and a failed send is retried."""

from datetime import UTC, datetime, timedelta

import pytest

from recruiting.digest import MAX_LINES, DigestFailed, InMemoryDigestLog, ReminderDigest
from recruiting.domain import Channel, Role
from recruiting.identity import CurrentUser, InMemoryUserDirectory
from recruiting.outreach import DeliveryResult, DeliveryStatus, OutboundMessage
from recruiting.records.models import FollowUp, FollowUpKind

# Wednesday 2026-11-25, 07:30 in New York (12:30 UTC).
NOW = datetime(2026, 11, 25, 12, 30, tzinfo=UTC)

ADMIN = CurrentUser("u-admin", "admin@cbs.example", "Ada Admin", Role.ADMINISTRATOR)
RECRUITER = CurrentUser("u-rec", "rec@cbs.example", "Rae Recruiter", Role.RECRUITER)
OTHER_RECRUITER = CurrentUser("u-rec2", "rec2@cbs.example", "Rob Recruiter", Role.RECRUITER)
BD = CurrentUser("u-bd", "bd@cbs.example", "Bea BD", Role.BD_MANAGER)
GONE = CurrentUser("u-gone", "gone@cbs.example", "Former", Role.RECRUITER, active=False)
CLIENT = CurrentUser("u-client", "c@client.example", "Client", Role.CLIENT, "company-1")


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class Outbox:
    channel = Channel.EMAIL

    def __init__(self, fail_for=()):
        self.sent: list[OutboundMessage] = []
        self.fail_for = set(fail_for)

    def deliver(self, message):
        if message.recipient in self.fail_for:
            raise ConnectionError("mailbox unreachable")
        self.sent.append(message)
        return DeliveryResult(DeliveryStatus.SENT, f"m-{len(self.sent)}")

    def to(self, email):
        return [m for m in self.sent if m.recipient == email]


class FollowUps:
    """Same ownership rule as FollowUpAgent.for_user."""

    def __init__(self, items):
        self.items = items

    def for_user(self, role, user_id):
        return [
            i for i in self.items
            if role == "administrator"
            or (role in i.owner_role.split(",") and i.owner_id in (None, user_id))
        ]  # fmt: skip


def item(n, due, owner_role="recruiter", owner_id="u-rec", kind=FollowUpKind.CANDIDATE_OUTREACH):
    return FollowUp(
        id=f"f{n}", kind=kind, subject_id=f"s{n}", owner_role=owner_role, owner_id=owner_id,
        anchor_at=NOW - timedelta(days=10), due_at=due,
        note=f"Candidate contacted; no reply yet (Candidate {n}, JOB-{n})",
    )  # fmt: skip


def make(items, outbox=None, clock=None, users=None, **kw):
    outbox = outbox or Outbox()
    directory = InMemoryUserDirectory(
        users or [ADMIN, RECRUITER, OTHER_RECRUITER, BD, GONE, CLIENT]
    )
    digest = ReminderDigest(
        FollowUps(items), directory, outbox, InMemoryDigestLog(),
        web_base_url="https://rap.cbs.example/", clock=clock or Clock(), **kw,
    )  # fmt: skip
    return digest, outbox


ITEMS = [
    item(1, NOW - timedelta(days=3)),  # overdue
    item(2, NOW + timedelta(hours=4)),  # due later today
    item(3, NOW + timedelta(days=2)),  # not yet: left out
    item(4, None),  # no date: left out
    item(5, NOW - timedelta(days=1), owner_id="u-rec2"),  # the other recruiter's
]


def test_each_staff_user_gets_their_own_due_and_overdue_items():
    digest, outbox = make(ITEMS)
    report = digest.run()
    assert (report.sent, report.nothing_due, report.failed) == (3, 1, 0)  # BD has nothing due

    [mine] = outbox.to(RECRUITER.email)
    assert mine.approval_request_id == ""  # internal mail: no approval to trace
    assert mine.content["subject"] == "Follow-ups for Wed 25 Nov: 1 overdue, 1 due today"
    body = mine.content["body"]
    assert body.startswith("Hello Rae Recruiter,")
    assert (
        "Overdue (1)\n- Candidate reply: Candidate contacted; no reply yet (Candidate 1, JOB-1)"
        in body
    )
    assert "(due 22 Nov, 3 days late)" in body
    assert "Due today (1)" in body and "(due 11:30)" in body  # local time, New York
    assert "Candidate 3" not in body and "Candidate 4" not in body and "Candidate 5" not in body
    assert "Open your follow-up list: https://rap.cbs.example/follow-ups" in body

    [theirs] = outbox.to(OTHER_RECRUITER.email)
    assert "Candidate 5" in theirs.content["body"] and "Candidate 1" not in theirs.content["body"]

    # The Administrator owns no follow-ups: they get what is late across everyone.
    [admin] = outbox.to(ADMIN.email)
    assert admin.content["subject"].endswith(": 2 overdue")
    assert "Overdue follow-ups across all staff" in admin.content["body"]
    assert "Candidate 2" not in admin.content["body"]  # due today, not late

    assert not outbox.to(BD.email) and not outbox.to(GONE.email) and not outbox.to(CLIENT.email)


def test_sent_once_a_day_at_the_digest_hour_on_weekdays():
    clock = Clock(NOW.replace(hour=11, minute=59))  # 06:59 in New York
    digest, outbox = make(ITEMS, clock=clock)
    assert digest.run().not_time_yet == 1 and outbox.sent == []

    clock.now = NOW  # 07:30
    assert digest.run().sent == 3
    clock.now += timedelta(hours=5)  # hourly runs later the same day
    report = digest.run()
    assert (report.sent, report.already_sent) == (0, 3)

    clock.now = datetime(2026, 11, 28, 13, 0, tzinfo=UTC)  # Saturday
    assert digest.run().not_time_yet == 1
    clock.now = datetime(2026, 11, 30, 12, 0, tzinfo=UTC)  # Monday 07:00
    assert digest.run().sent == 3
    assert len(outbox.to(RECRUITER.email)) == 2


def test_weekends_and_another_hour_and_timezone_are_settings():
    saturday = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)  # 09:00 London
    digest, outbox = make(ITEMS, clock=Clock(saturday), weekends=True, hour=9,
                          timezone="Europe/London")  # fmt: skip
    assert digest.run().sent == 3
    with pytest.raises(ValueError):
        make(ITEMS, hour=24)


def test_a_failed_send_does_not_stop_the_others_and_is_retried():
    outbox = Outbox(fail_for={RECRUITER.email})
    clock = Clock()
    digest, _ = make(ITEMS, outbox=outbox, clock=clock)
    with pytest.raises(DigestFailed, match="1 of 3 reminder digests not sent"):
        digest.run()
    assert {m.recipient for m in outbox.sent} == {ADMIN.email, OTHER_RECRUITER.email}

    outbox.fail_for.clear()
    clock.now += timedelta(hours=1)
    report = digest.run()  # only the one that failed goes out now
    assert (report.sent, report.already_sent) == (1, 2)
    assert len(outbox.to(RECRUITER.email)) == 1


def test_long_lists_are_cut_with_a_count_of_the_rest():
    many = [item(n, NOW - timedelta(hours=n)) for n in range(1, MAX_LINES + 6)]
    digest, outbox = make(many, users=[RECRUITER])
    digest.run()
    body = outbox.sent[0].content["body"]
    assert body.count("\n- ") == MAX_LINES
    assert "... and 5 more." in body


def test_needs_an_email_channel():
    class LinkedIn(Outbox):
        channel = Channel.LINKEDIN

    with pytest.raises(ValueError):
        make(ITEMS, outbox=LinkedIn())


def test_internal_email_has_no_approval_header_or_unsubscribe_link():
    from recruiting.api.services import build_email_channels
    from recruiting.config import Settings

    settings = Settings(email_providers=["gmail"], email_sender="bd@cbs.example", _env_file=None)
    [outreach] = build_email_channels(settings)
    [internal] = build_email_channels(settings, unsubscribe=False)
    message = OutboundMessage("", Channel.EMAIL, "rec@cbs.example", {"subject": "s", "body": "b"})
    mime = internal.mime(message)
    assert mime["X-RAP-Approval-Id"] is None and mime["List-Unsubscribe"] is None
    approved = OutboundMessage(
        "a-1", Channel.EMAIL, "lee@client.example", {"subject": "s", "body": "b"}
    )
    mime = outreach.mime(approved)
    assert mime["X-RAP-Approval-Id"] == "a-1" and mime["List-Unsubscribe"]


def test_microsoft_365_leaves_out_the_approval_header_for_internal_mail():
    import httpx

    from recruiting.outreach import Microsoft365EmailChannel

    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(202)

    http = httpx.Client(base_url="https://graph.test", transport=httpx.MockTransport(handler))
    channel = Microsoft365EmailChannel("bd@cbs.example", lambda: "token", http)
    channel.deliver(OutboundMessage("", Channel.EMAIL, "rec@cbs.example", {"subject": "s"}))
    channel.deliver(OutboundMessage("a-1", Channel.EMAIL, "lee@client.example", {"subject": "s"}))
    internal, approved = (__import__("json").loads(r.content)["message"] for r in sent)
    assert "internetMessageHeaders" not in internal
    assert approved["internetMessageHeaders"] == [{"name": "X-RAP-Approval-Id", "value": "a-1"}]


def test_scheduler_runs_the_digest_when_it_is_on():
    from recruiting.api.services import build_services
    from recruiting.config import Settings
    from recruiting.scheduler import default_jobs

    off = build_services(Settings(demo_data=True, _env_file=None))
    assert off.digest is None
    assert "reminder_digest" not in [j.name for j in default_jobs(off)]

    services = build_services(Settings(
        demo_data=True, reminder_digest=True, digest_hour=0, digest_weekends=True, _env_file=None,
    ))  # fmt: skip
    [job] = [j for j in default_jobs(services) if j.name == "reminder_digest"]
    assert job.interval == timedelta(hours=1)
    report = job.run()  # demo pipeline: staff with work due get a digest in the demo outbox
    assert report.failed == 0 and report.sent + report.nothing_due == 5


def test_without_an_email_provider_the_digest_cannot_be_turned_on():
    from recruiting.api.services import build_services
    from recruiting.config import Settings

    with pytest.raises(RuntimeError, match="RAP_EMAIL_PROVIDERS"):
        build_services(Settings(reminder_digest=True, _env_file=None))
