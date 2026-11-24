"""Mailbox reader: replies, JDs, bounce reports and auto-replies are read from the outreach
mailbox and routed once each (plan sections 7, 9 and 14; scenarios S6 and S9)."""

import base64
import json
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import httpx
import pytest

pytest.importorskip("langgraph")

from test_documents import make_pdf  # noqa: E402
from test_phase8_end_to_end import (  # noqa: E402
    ADMIN,
    BD,
    CSV,
    JD,
    REC,
    approve,
    build,
    h,
    ok,
)

from recruiting.mailbox import (  # noqa: E402
    MAX_ATTEMPTS,
    GmailMailbox,
    InMemoryProcessed,
    MailboxReader,
    MailItem,
    Microsoft365Mailbox,
    parse,
)
from recruiting.scheduler import default_jobs  # noqa: E402

RACHEL = "r.lee@northfield-axle.example"
SENDER = "bd@cbs.example"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def mail(sender, subject, body, message_id, attachments=(), **headers) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, SENDER, subject
    msg["Message-ID"], msg["Date"] = message_id, "Tue, 24 Nov 2026 11:58:00 +0000"
    for key, value in headers.items():
        msg[key.replace("_", "-")] = value
    msg.set_content(body)
    for name, data, ctype in attachments:
        main, sub = ctype.split("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return bytes(msg)


def dsn(recipient, approval_id, status="5.1.1", action="failed") -> bytes:
    """An RFC 3464 delivery status notification, as Gmail and Exchange send them."""
    return f"""From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>
To: {SENDER}
Subject: Delivery Status Notification (Failure)
Message-ID: <dsn-{approval_id}@mx.google.com>
Date: Tue, 24 Nov 2026 11:59:00 +0000
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="B"

--B
Content-Type: text/plain

Address not found.
--B
Content-Type: message/delivery-status

Reporting-MTA: dns; googlemail.com

Final-Recipient: rfc822; {recipient}
Action: {action}
Status: {status}
Diagnostic-Code: smtp; 550 {status} The email account does not exist

--B
Content-Type: text/rfc822-headers

From: {SENDER}
To: {recipient}
Subject: Engineering talent
X-RAP-Approval-Id: {approval_id}

--B--
""".encode()


class FakeMailbox:
    """Unread messages; `sticky` keeps messages unread (provider mark failed) to test de-dup."""

    def __init__(self, sticky=False):
        self.unread: dict[str, bytes] = {}
        self.done: list[str] = []
        self.sticky = sticky

    def add(self, raw):
        self.unread[f"p{len(self.unread) + len(self.done)}"] = raw

    def fetch(self, limit):
        return [MailItem(k, v) for k, v in list(self.unread.items())[:limit]]

    def mark_done(self, provider_id):
        self.done.append(provider_id)
        if not self.sticky:
            self.unread.pop(provider_id, None)


def setup(sticky=False):
    client, services, email, clock = build()
    mailbox = FakeMailbox(sticky)
    reader = MailboxReader(mailbox, services.platform, InMemoryProcessed(),
                           own_addresses=(SENDER,), clock=clock)  # fmt: skip
    services.mailbox = reader
    ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV}, headers=h(BD)))
    ok(client.post("/companies/hiring-signals", json={}, headers=h(BD)))
    [first] = ok(client.post("/companies/outreach-drafts", json={}, headers=h(BD)))["drafted"]
    approve(client, first["approval_request_id"], BD)
    return client, services, mailbox, reader, first


def test_client_reply_with_a_pdf_jd_becomes_a_job_once():
    client, services, mailbox, reader, first = setup(sticky=True)
    mailbox.add(mail(RACHEL, "Re: Engineering talent", "JD attached, thanks.", "<jd-1@client>",
                     [("QE.pdf", make_pdf(JD.split(". ")), "application/pdf")]))  # fmt: skip
    report = reader.run()
    assert report.fetched == 1 and report.routed == 1 and report.failed == 0
    [job] = services.platform.jobs.list()
    assert {"PFMEA", "PPAP", "APQP"} <= set(job.analysis["must_have_skills"])
    assert services.platform.deliveries.get(first["approval_request_id"]).replied_at is not None

    again = reader.run()  # the provider still shows it unread: handled once only
    assert again.duplicates == 1 and again.routed == 0
    assert len(services.platform.jobs.list()) == 1


def test_bounce_report_marks_the_contact_invalid():
    client, services, mailbox, reader, first = setup()
    mailbox.add(dsn(RACHEL, first["approval_request_id"]))
    report = reader.run()
    assert report.bounces == 1 and mailbox.unread == {}
    receipt = services.platform.deliveries.get(first["approval_request_id"])
    assert receipt.status == "bounced" and "5.1.1" in receipt.error
    assert services.platform.companies.contact_by_email(RACHEL).email_valid is False

    # A delay notice (4.x.x) is a soft bounce and changes nothing.
    _, services2, mailbox2, reader2, first2 = setup()
    mailbox2.add(dsn(RACHEL, first2["approval_request_id"], status="4.4.7", action="delayed"))
    assert reader2.run().bounces == 1
    assert services2.platform.deliveries.get(first2["approval_request_id"]).status == "sent"


def test_bounce_with_an_unknown_approval_is_matched_by_recipient():
    _, services, mailbox, reader, first = setup()
    mailbox.add(dsn(RACHEL, "00000000-0000-0000-0000-000000000000"))
    assert reader.run().bounces == 1
    assert services.platform.deliveries.get(first["approval_request_id"]).status == "bounced"


def test_auto_reply_headers_mean_out_of_office_even_without_the_words():
    client, services, mailbox, reader, first = setup()
    mailbox.add(mail(RACHEL, "Re: Engineering talent", "Thanks for your message.",
                     "<auto-1@client>", Auto_Submitted="auto-replied"))  # fmt: skip
    report = reader.run()
    assert report.auto_replies == 1 and report.routed == 1
    assert services.platform.deliveries.get(first["approval_request_id"]).replied_at is None
    ok(client.post("/follow-ups/run", headers=h(ADMIN)))
    kinds = {i["kind"] for i in ok(client.get("/follow-ups", headers=h(BD)))}
    assert "client_outreach" in kinds  # S9: the reminder stays


def test_candidate_replies_go_to_agent9_and_own_copies_are_ignored():
    client, services, mailbox, reader, _ = setup()
    resume = "Priya Natarajan\npriya@mail.example | (313) 555-0108\nQuality Engineer, PFMEA, PPAP."
    ok(client.post("/candidates", json={"resumes": [{"resume_text": resume, "source": "ats"}]},
                   headers=h(REC)), 201)  # fmt: skip
    mailbox.add(mail("Priya <priya@mail.example>", "Re: opportunity", "Please unsubscribe me.",
                     "<c-1@mail>"))  # fmt: skip
    mailbox.add(mail("priya@mail.example", "Away", "Back Monday.", "<c-2@mail>",
                     X_Autoreply="yes"))  # fmt: skip
    mailbox.add(mail(SENDER, "Engineering talent", "Our own sent copy.", "<own@cbs>"))
    report = reader.run()
    assert report.routed == 2 and report.own_messages == 1 and report.auto_replies == 1
    assert services.opt_outs.is_opted_out("priya@mail.example")


def test_a_failing_message_is_retried_then_left_for_a_person():
    client, services, mailbox, reader, _ = setup()

    def broken(email, candidate=None):
        raise RuntimeError("database unreachable")

    services.platform.jd_intake.process = broken
    mailbox.add(mail("someone@new.example", "Hello", "Hi there", "<x-1@new>"))
    for attempt in range(1, MAX_ATTEMPTS + 1):
        report = reader.run()
        assert report.failed == 1
        assert ("p0" in mailbox.unread) == (attempt < MAX_ATTEMPTS)  # read only after the last try
    assert reader.run().fetched == 0
    ops = ok(client.get("/admin/operations", headers=h(ADMIN)))
    assert any("1 inbound email could not be processed" in a for a in ops["alerts"])


def test_scheduler_runs_the_mailbox_every_two_minutes():
    _, services, _, reader, _ = setup()
    jobs = {j.name: j for j in default_jobs(services)}
    assert jobs["mailbox"].interval == timedelta(minutes=2) and jobs["mailbox"].run == reader.run


def test_parse_html_only_and_missing_message_id():
    msg = EmailMessage()
    msg["From"], msg["Subject"] = "a@b.example", "Hi"
    msg.set_content("<p>Hello&nbsp;<b>there</b></p><script>x()</script>", subtype="html")
    parsed = parse(bytes(msg), "prov-1", NOW)
    assert parsed.body.split() == ["Hello", "there"]
    assert parsed.message_id == "<prov-1@mailbox>" and parsed.received_at == NOW
    weird = parse(b"From: mailer-daemon@x.example\nSubject: failure\n\nno report here", "p", NOW)
    assert weird.bounce is None and weird.looks_like_bounce


# ----- provider adapters ----------


def test_gmail_mailbox_reads_raw_messages_and_marks_them_read():
    raw = mail(RACHEL, "Re: x", "Hello", "<g-1@client>")
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m2"}, {"id": "m1"}]})
        if request.url.path.endswith("/modify"):
            assert json.loads(request.content) == {"removeLabelIds": ["UNREAD"]}
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"raw": base64.urlsafe_b64encode(raw).decode().rstrip("=")})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://g.test")
    box = GmailMailbox(lambda: "t", client=http)
    items = box.fetch(10)
    assert [i.provider_id for i in items] == ["m1", "m2"]  # oldest first
    assert items[0].raw == raw
    assert calls[0][2] == {"q": "in:inbox is:unread", "maxResults": "10"}
    box.mark_done("m1")
    assert calls[-1][:2] == ("POST", "/gmail/v1/users/me/messages/m1/modify")


def test_microsoft365_mailbox_reads_mime_and_marks_it_read():
    raw = mail(RACHEL, "Re: x", "Hello", "<o-1@client>")
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/mailFolders/inbox/messages"):
            assert request.url.params["$filter"] == "isRead eq false"
            return httpx.Response(200, json={"value": [
                {"id": "b", "receivedDateTime": "2026-11-24T11:00:00Z"},
                {"id": "a", "receivedDateTime": "2026-11-24T10:00:00Z"}]})  # fmt: skip
        if request.url.path.endswith("/$value"):
            return httpx.Response(200, content=raw)
        assert json.loads(request.content) == {"isRead": True}
        return httpx.Response(200, json={})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://graph.test")
    box = Microsoft365Mailbox("bd@cbs.example", lambda: "t", client=http)
    assert [i.provider_id for i in box.fetch(10)] == ["a", "b"]
    box.mark_done("a")
    assert seen[-1] == ("PATCH", "/users/bd@cbs.example/messages/a")
