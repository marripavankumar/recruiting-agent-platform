"""Phase 5: Agent 8 outreach, LinkedIn tasks, Agent 9 resume collection, through the API."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from recruiting.agents.candidate_outreach import LINKEDIN_NOTE_LIMIT, linkedin_note  # noqa: E402
from recruiting.api.app import create_app  # noqa: E402
from recruiting.api.platform import build_platform  # noqa: E402
from recruiting.api.services import Services  # noqa: E402
from recruiting.approvals import ApprovalEngine  # noqa: E402
from recruiting.compliance import OptOutRegistry  # noqa: E402
from recruiting.config import Settings  # noqa: E402
from recruiting.domain import Channel, Role  # noqa: E402
from recruiting.identity import CurrentUser, DevHeaderAuthenticator, InMemoryUserDirectory  # noqa: E402
from recruiting.outreach import (
    DeliveryResult,
    DeliveryStatus,
    ManualLinkedInChannel,
    OutreachService,
)  # noqa: E402
from recruiting.records.models import Candidate, Job  # noqa: E402
from recruiting.workflows.outreach_graph import OutreachWorkflow, build_outreach_graph  # noqa: E402

REC = CurrentUser("u-rec", "rec@cbs.example", "Dana Okafor", Role.RECRUITER)
BD = CurrentUser("u-bd", "bd@cbs.example", "BD", Role.BD_MANAGER)
SECRET = "hook-secret-123"

JOB = Job("job-1", "JOB-2026-0142", "client-1", "CAE Engineer - Crash", location="Troy, MI",
          industry="Automotive", analysis={"must_have_skills": ["LS-DYNA", "ANSA"]})  # fmt: skip


def cand(cid, name, email=None, linkedin=None, **kw):
    return Candidate(
        cid, name, "ats", email=email, linkedin_url=linkedin, skills=["LS-DYNA", "ANSA"], **kw
    )


class FakeEmail:
    channel = Channel.EMAIL

    def __init__(self):
        self.sent = []

    def deliver(self, message):
        self.sent.append(message)
        return DeliveryResult(DeliveryStatus.SENT, f"m-{len(self.sent)}")


@pytest.fixture
def ctx():
    settings = Settings(llm_provider="none", inbound_webhook_secret=SECRET, linkedin_weekly_limit=3,
                        public_base_url="https://rap.example", _env_file=None)  # fmt: skip
    users = InMemoryUserDirectory([REC, BD])
    approvals, opt_outs, email = ApprovalEngine(), OptOutRegistry(), FakeEmail()
    outreach = OutreachService(approvals, opt_outs, [email, ManualLinkedInChannel()])
    workflows = OutreachWorkflow(build_outreach_graph(approvals, outreach, MemorySaver()))
    platform = build_platform(settings, users=users, opt_outs=opt_outs, deliveries=outreach.log,
                              workflows=workflows, approvals=approvals)  # fmt: skip
    platform.jobs.add(JOB)
    for c in [
        cand(
            "c-john", "John Mercer", "john@mail.example", "https://linkedin.com/in/john-mercer-cae"
        ),
        cand("c-elena", "Elena Vasquez", "elena@mail.example", "https://linkedin.com/in/elena-v"),
        cand("c-out", "Opted Out", "out@mail.example", "https://linkedin.com/in/opted-out"),
        cand("c-nolink", "No Link", "nolink@mail.example"),
    ]:
        platform.candidates.add(c)
    opt_outs.record("out@mail.example", "reply")
    services = Services(
        approvals, opt_outs, users, DevHeaderAuthenticator(), workflows, outreach, platform
    )
    return TestClient(create_app(services)), services, email


def as_rec():
    return {"X-Dev-User-Email": REC.email}


def approve(client, approval_id):
    r = client.post(
        f"/approvals/{approval_id}/decision", json={"action": "approve"}, headers=as_rec()
    )
    assert r.status_code == 200, r.text


def engagement_for(client, candidate_id):
    data = client.get(f"/jobs/{JOB.id}/engagements", headers=as_rec()).json()
    return next(e for e in data["engagements"] if e["candidate_id"] == candidate_id), data[
        "summary"
    ]


def test_linkedin_note_follows_the_brd_template_and_limit():
    note = linkedin_note(JOB, cand("x", "John Mercer"), "Dana Okafor")
    assert note.startswith("Hello John,") and "searching for CAE Engineers" in note
    assert (
        "Would you be interested in discussing them?" in note and len(note) <= LINKEDIN_NOTE_LIMIT
    )
    long = linkedin_note(JOB, cand("x", "J" * 200), "Dana Okafor")
    assert len(long) <= LINKEDIN_NOTE_LIMIT


def test_outreach_needs_approval_then_becomes_a_linkedin_task(ctx):
    client, services, email = ctx
    body = {
        "candidate_ids": ["c-john", "c-out", "c-nolink", "nobody"],
        "channels": ["linkedin", "email"],
    }
    report = client.post(f"/jobs/{JOB.id}/outreach", json=body, headers=as_rec()).json()
    assert {d["channel"] for d in report["drafted"] if d["candidate_id"] == "c-john"} == {
        "linkedin",
        "email",
    }
    assert report["skipped"]["opted_out"] == ["c-out"] and report["skipped"][
        "no_linkedin_profile"
    ] == ["c-nolink"]
    assert report["skipped"]["unknown_candidate"] == ["nobody"]
    # Nothing sent yet: both drafts wait in the recruiter's queue.
    assert email.sent == [] and client.get("/linkedin-tasks", headers=as_rec()).json() == []
    queue = client.get("/approvals/pending", headers=as_rec()).json()
    gates = sorted(r["gate"] for r in queue)
    assert gates == [
        "candidate_outreach_email",
        "candidate_outreach_email",
        "linkedin_connection_request",
    ]
    # The client is not named in candidate emails before consent.
    assert all("client-1" not in r["content"].get("body", "") for r in queue)
    again = client.post(
        f"/jobs/{JOB.id}/outreach", json={"candidate_ids": ["c-john"]}, headers=as_rec()
    ).json()
    assert again["skipped"] == {"already_drafted": ["c-john"]}

    linkedin_id = next(
        d["approval_request_id"] for d in report["drafted"] if d["channel"] == "linkedin"
    )
    approve(client, linkedin_id)
    [task] = client.get("/linkedin-tasks", headers=as_rec()).json()
    assert task["candidate_name"] == "John Mercer" and task["note"].startswith("Hello John")
    sent = client.post(f"/linkedin-tasks/{linkedin_id}/sent", headers=as_rec()).json()
    assert sent["status"] == "contacted"
    assert client.get("/linkedin-tasks", headers=as_rec()).json() == []
    assert services.outreach.receipt(linkedin_id).status is DeliveryStatus.MANUALLY_SENT


def test_rejected_draft_and_weekly_linkedin_limit(ctx):
    client, services, _ = ctx
    report = client.post(
        f"/jobs/{JOB.id}/outreach", json={"candidate_ids": ["c-elena"]}, headers=as_rec()
    ).json()
    approval_id = report["drafted"][0]["approval_request_id"]
    client.post(
        f"/approvals/{approval_id}/decision",
        json={"action": "reject", "reason": "Wrong level"},
        headers=as_rec(),
    )
    assert engagement_for(client, "c-elena")[0]["status"] == "draft_rejected"
    # A rejected draft can be redone.
    redo = client.post(
        f"/jobs/{JOB.id}/outreach", json={"candidate_ids": ["c-elena"]}, headers=as_rec()
    ).json()
    assert len(redo["drafted"]) == 1
    # Weekly limit is 3 in this fixture: the pending drafts count towards it.
    services.platform.candidates.add(cand("c-a", "A A", linkedin="https://linkedin.com/in/a"))
    services.platform.candidates.add(cand("c-b", "B B", linkedin="https://linkedin.com/in/b"))
    services.platform.candidates.add(cand("c-c", "C C", linkedin="https://linkedin.com/in/c"))
    more = client.post(
        f"/jobs/{JOB.id}/outreach", json={"candidate_ids": ["c-a", "c-b", "c-c"]}, headers=as_rec()
    ).json()
    assert len(more["drafted"]) == 2 and more["skipped"] == {"linkedin_weekly_limit": ["c-c"]}


def contacted_by_email(client, candidate_id):
    report = client.post(f"/jobs/{JOB.id}/outreach", json={"candidate_ids": [candidate_id], "channels": ["email"]},
                         headers=as_rec()).json()  # fmt: skip
    approve(client, report["drafted"][0]["approval_request_id"])
    engagement, _ = engagement_for(client, candidate_id)
    assert engagement["status"] == "contacted"
    return engagement


def test_interest_starts_agent9_and_the_secure_link_collects_everything(ctx):
    client, services, email = ctx
    engagement = contacted_by_email(client, "c-john")
    out = client.post(
        f"/engagements/{engagement['id']}/outcome", json={"outcome": "interested"}, headers=as_rec()
    ).json()
    assert out["status"] == "details_requested" and out["upload_link"].startswith(
        "https://rap.example/collect/"
    )
    assert "consent" in out["missing"] and "resume" in out["missing"]
    token = out["upload_link"].rsplit("/", 1)[1]
    # The request email itself waits for approval, then goes out with the link.
    approve(client, out["approvals"]["details_request"])
    assert out["upload_link"] in email.sent[-1].content["body"]
    assert "token_hash" not in out

    page = client.get(f"/collect/{token}")
    assert (
        page.status_code == 200
        and "CAE Engineer - Crash" in page.text
        and "consent_submit" in page.text
    )
    partial = client.post(
        f"/collect/{token}", data={"availability": "2 weeks", "expected_rate": "$80/h"}
    )
    assert "We still need" in partial.text
    full = {
        "resume_text": "John Mercer\njohn@mail.example\n7 years crash CAE: LS-DYNA, ANSA, FMVSS.",
        "visa_status": "Green card", "current_location": "Auburn Hills, MI", "notice_period": "2 weeks",
        "work_authorization": "Green card holder", "preferred_location": "Troy, MI",
        "consent_store": "yes", "consent_submit": "yes",
    }  # fmt: skip
    done = client.post(f"/collect/{token}", data=full)
    assert "has everything needed" in done.text
    engagement, summary = engagement_for(client, "c-john")
    assert (
        engagement["status"] == "complete"
        and engagement["consent_recorded"]
        and engagement["missing"] == []
    )
    assert summary["completion_rate"] == 1.0
    candidate = services.platform.candidates.get("c-john")
    assert candidate.consent_status == "granted" and candidate.expected_rate == "$80/h"
    assert "FMVSS" in candidate.skills  # the uploaded resume was parsed into the same record

    assert client.get("/collect/not-a-real-token").status_code == 400


def test_expired_link_is_refused(ctx):
    client, services, _ = ctx
    engagement = contacted_by_email(client, "c-elena")
    out = client.post(f"/engagements/{engagement['id']}/request-details", headers=as_rec()).json()
    token = out["upload_link"].rsplit("/", 1)[1]
    stored = services.platform.engagements.get(engagement["id"])
    stored.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    services.platform.engagements.save(stored)
    assert "expired" in client.get(f"/collect/{token}").text


def test_candidate_email_replies_go_to_agent9(ctx):
    client, services, _ = ctx
    contacted_by_email(client, "c-john")
    reply = {
        "from_address": "John@Mail.example", "subject": "Re: CAE Engineer opportunity",
        "body": "Yes, interested! Green card holder, $80/hr C2C, 2 weeks notice. I consent to you submitting me.",
        "attachments_text": "John Mercer\nCAE engineer, 7 years LS-DYNA, ANSA, Primer.", "message_id": "<r1@mail>",
    }  # fmt: skip
    result = client.post(
        "/inbound/email", json=reply, headers={"X-RAP-Webhook-Secret": SECRET}
    ).json()
    assert result["handled_by"] == "agent-9" and result["intent"] == "interested"
    assert {"resume", "expected_rate", "notice_period", "consent"} <= set(result["updated"])
    assert result["details_request_id"]  # Agent 9 drafted the request for what is still missing
    engagement, _ = engagement_for(client, "c-john")
    assert engagement["status"] == "details_requested" and "consent" not in engagement["missing"]
    assert "current_location" in engagement["missing"]

    bye = {
        **reply,
        "body": "Please remove me from your list",
        "attachments_text": "",
        "message_id": "<r2@mail>",
    }
    assert (
        client.post("/inbound/email", json=bye, headers={"X-RAP-Webhook-Secret": SECRET}).json()[
            "intent"
        ]
        == "unsubscribe"
    )
    assert services.opt_outs.is_opted_out("john@mail.example")
    assert services.opt_outs.is_opted_out("https://linkedin.com/in/john-mercer-cae")


def test_roles(ctx):
    client, _, _ = ctx
    bd = {"X-Dev-User-Email": BD.email}
    assert (
        client.post(
            f"/jobs/{JOB.id}/outreach", json={"candidate_ids": ["c-john"]}, headers=bd
        ).status_code
        == 403
    )
    assert client.get("/linkedin-tasks", headers=bd).status_code == 403
    assert client.get(f"/jobs/{JOB.id}/engagements", headers=bd).status_code == 403
