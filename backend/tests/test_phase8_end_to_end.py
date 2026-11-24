"""Phase 8: end-to-end scenario S1 through the HTTP API, KPIs, operations, S16, S23 and S26."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from recruiting.api.app import create_app  # noqa: E402
from recruiting.api.platform import build_platform  # noqa: E402
from recruiting.api.services import Services  # noqa: E402
from recruiting.approvals import ApprovalEngine  # noqa: E402
from recruiting.compliance import OptOutRegistry  # noqa: E402
from recruiting.config import Settings  # noqa: E402
from recruiting.domain import Channel, GateType, Role  # noqa: E402
from recruiting.identity import CurrentUser, DevHeaderAuthenticator, InMemoryUserDirectory  # noqa: E402
from recruiting.outreach import (  # noqa: E402
    DeliveryResult,
    DeliveryStatus,
    ManualLinkedInChannel,
    OutreachService,
)
from recruiting.records.models import (  # noqa: E402
    ConsentStatus,
    Engagement,
    EngagementStatus,
    Job,
)
from recruiting.workflows.outreach_graph import OutreachWorkflow, build_outreach_graph  # noqa: E402

ADMIN = CurrentUser("u-admin", "admin@cbs.example", "Admin", Role.ADMINISTRATOR)
BD = CurrentUser("u-bd", "bd@cbs.example", "Marcus Hale", Role.BD_MANAGER)
REC = CurrentUser("u-rec", "rec@cbs.example", "Dana Okafor", Role.RECRUITER)
AM = CurrentUser("u-am", "am@cbs.example", "Leah Kim", Role.ACCOUNT_MANAGER)
SALES = CurrentUser("u-sales", "sales@cbs.example", "Tom Becker", Role.SALES_MANAGER)
STAFF = [ADMIN, BD, REC, AM, SALES]
SECRET = "hook-secret-123"
HOOK = {"X-RAP-Webhook-Secret": SECRET}

CSV = """company,domain,industry,revenue_usd,country,location,career_page_url,contact_name,contact_title,contact_email
Northfield Axle Systems,northfield-axle.example,Automotive,1.2B,US,"Troy, MI",https://northfield-axle.example/careers,Rachel Lee,Engineering Manager,r.lee@northfield-axle.example
Other Corp,other.example,Automotive,2B,US,"Flint, MI",,Kim Wu,HR Manager,kim@other.example
"""
CAREERS = """<script type="application/ld+json">[
{"@type":"JobPosting","title":"Quality Engineer","url":"https://northfield-axle.example/j/1","datePosted":"2026-11-20"},
{"@type":"JobPosting","title":"Quality Engineer II","url":"https://northfield-axle.example/j/2","datePosted":"2026-11-19"},
{"@type":"JobPosting","title":"Supplier Quality Engineer","url":"https://northfield-axle.example/j/3","datePosted":"2026-11-18"}
]</script>"""
JD = (
    "Job description: Quality Engineer. Required: PFMEA, PPAP, APQP. 5+ years of automotive "
    "quality experience. Location: Troy, MI."
)


def resume(name, email, years):
    return (
        f"{name}\nTroy, MI | {email} | (313) 555-01{years:02d}\n"
        f"Quality Engineer with {years} years of experience in PFMEA, PPAP, APQP and 8D.\n"
        "B.S. Mechanical Engineering"
    )


class FakeEmail:
    channel = Channel.EMAIL

    def __init__(self):
        self.sent = []

    def deliver(self, message):
        self.sent.append(message)
        return DeliveryResult(DeliveryStatus.SENT, f"m-{len(self.sent)}")


class Fetcher:
    def fetch(self, url):
        return CAREERS if "northfield" in url else None


class Clock:
    """Real time plus an adjustable offset (deliveries are stamped in real time)."""

    def __init__(self):
        self.offset = timedelta()

    @property
    def now(self):
        return datetime.now(UTC) + self.offset

    def __call__(self):
        return self.now


def build():
    clock = Clock()
    settings = Settings(llm_provider="none", inbound_webhook_secret=SECRET,
                        public_base_url="https://rap.example", _env_file=None)  # fmt: skip
    users = InMemoryUserDirectory(STAFF)
    approvals, opt_outs, email = ApprovalEngine(), OptOutRegistry(), FakeEmail()
    outreach = OutreachService(approvals, opt_outs, [email, ManualLinkedInChannel()])
    workflows = OutreachWorkflow(build_outreach_graph(approvals, outreach, MemorySaver()))
    platform = build_platform(settings, users=users, opt_outs=opt_outs, deliveries=outreach.log,
                              workflows=workflows, approvals=approvals, fetcher=Fetcher(),
                              clock=clock)  # fmt: skip
    services = Services(
        approvals, opt_outs, users, DevHeaderAuthenticator(), workflows, outreach, platform
    )
    return TestClient(create_app(services)), services, email, clock


@pytest.fixture
def ctx():
    return build()


def h(user):
    return {"X-Dev-User-Email": user.email}


def ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


def approve(client, approval_id, user):
    ok(client.post(f"/approvals/{approval_id}/decision", json={"action": "approve"},
                   headers=h(user)))  # fmt: skip


def test_s1_happy_path_from_company_to_started(ctx):
    client, services, email, _ = ctx

    # 1-3. Agents 1-3: import, hiring signals, first email approved by the BD Manager and sent.
    ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV}, headers=h(BD)))
    ok(client.post("/companies/hiring-signals", json={}, headers=h(BD)))
    drafts = ok(client.post("/companies/outreach-drafts", json={}, headers=h(BD)))
    [first] = drafts["drafted"]
    approve(client, first["approval_request_id"], BD)
    assert [m.recipient for m in email.sent] == ["r.lee@northfield-axle.example"]
    company_id = first["company_id"]

    # 4-5. Agents 4-5: the reply with a JD becomes an analysed job; the Account Manager confirms.
    reply = {"from_address": "r.lee@northfield-axle.example",
             "subject": "Re: " + email.sent[0].content["subject"], "body": "JD attached.",
             "attachments_text": JD, "message_id": "<jd-1@mail>"}  # fmt: skip
    [job_id] = ok(client.post("/inbound/email", json=reply, headers=HOOK))["job_ids"]
    job = ok(client.patch(f"/jobs/{job_id}", json={"title": "Quality Engineer",
             "confirm_fields": ["title"]}, headers=h(AM)))  # fmt: skip
    assert {"PFMEA", "PPAP", "APQP"} <= set(job["analysis"]["must_have_skills"])

    # 6. Agents 6-7: candidates added, ranked and reviewed by the recruiter.
    people = [("Priya Natarajan", "priya@mail.example", 8), ("Wei Zhang", "wei@mail.example", 6),
              ("Raj Iyer", "raj@mail.example", 5)]  # fmt: skip
    ok(client.post("/candidates", json={"resumes": [
        {"resume_text": resume(n, e, y), "source": "ats"} for n, e, y in people]},
        headers=h(REC)), 201)  # fmt: skip
    ranking = ok(client.post(f"/jobs/{job_id}/ranking", headers=h(REC)))
    top = [c for c in ranking["candidates"] if c["rank"] is not None]
    assert len(top) == 3
    for c in top:
        ok(client.post(f"/jobs/{job_id}/ranking/{c['candidate_id']}/review",
                       json={"verdict": "qualified"}, headers=h(REC)))  # fmt: skip

    # 7. Agent 8: outreach approved by the recruiter and sent.
    ids = [c["candidate_id"] for c in top]
    drafted = ok(client.post(f"/jobs/{job_id}/outreach",
                             json={"candidate_ids": ids, "channels": ["email"]}, headers=h(REC)))  # fmt: skip
    for d in drafted["drafted"]:
        approve(client, d["approval_request_id"], REC)
    engagements = ok(client.get(f"/jobs/{job_id}/engagements", headers=h(REC)))["engagements"]
    assert {e["status"] for e in engagements} == {"contacted"}

    # 8. Agent 9: Priya replies with interest; the details request is approved and she fills
    # in the secure link.
    answer = {"from_address": "priya@mail.example", "subject": "Re: opportunity",
              "body": "Yes, interested!", "attachments_text": "", "message_id": "<c-1@mail>"}  # fmt: skip
    result = ok(client.post("/inbound/email", json=answer, headers=HOOK))
    approve(client, result["details_request_id"], REC)
    link = next(m for m in email.sent if m.recipient == "priya@mail.example" and "/collect/" in
                m.content["body"])  # fmt: skip
    token = re.search(r"/collect/([\w-]+)", link.content["body"]).group(1)
    form = {"availability": "2 weeks", "visa_status": "US citizen", "current_location": "Troy, MI",
            "expected_rate": "$75/hr", "notice_period": "2 weeks",
            "work_authorization": "US citizen", "preferred_location": "Detroit area",
            "consent_store": "yes", "consent_submit": "yes"}  # fmt: skip
    assert "everything needed" in client.post(f"/collect/{token}", data=form).text
    priya = result["candidate_id"]

    # 9-10. Agents 10-11: match, recruiter approval, package sent to the hiring manager.
    sub = ok(client.post(f"/jobs/{job_id}/submissions", json={"candidate_id": priya},
                         headers=h(REC)))  # fmt: skip
    sub = ok(client.post(f"/submissions/{sub['id']}/request-approval",
                         json={"right_to_represent": True, "acknowledge_flags": True},
                         headers=h(REC)))  # fmt: skip
    approve(client, sub["approval_request_id"], REC)
    assert email.sent[-1].recipient == "r.lee@northfield-axle.example"
    assert email.sent[-1].content["subject"].startswith("Candidate submission")

    # 11. Agent 12 and the client portal: interview, offer, start.
    rachel = CurrentUser("u-cl", "r.lee@northfield-axle.example", "Rachel Lee", Role.CLIENT,
                         company_id)  # fmt: skip
    services.users = InMemoryUserDirectory([*STAFF, rachel])
    [seen] = ok(client.get("/client/submissions", headers=h(rachel)))
    ok(client.post(f"/client/submissions/{seen['id']}/feedback", json={"decision": "interview"},
                   headers=h(rachel)))  # fmt: skip
    soon = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    start = (datetime.now(UTC) + timedelta(days=21)).date().isoformat()
    for body in ({"stage": "interview_scheduled", "interview_at": soon}, {"stage": "interviewed"},
                 {"stage": "offer_extended"}, {"stage": "offer_accepted", "start_date": start},
                 {"stage": "started"}):  # fmt: skip
        ok(client.post(f"/submissions/{seen['id']}/stage", json=body, headers=h(AM)))
    ok(client.post("/follow-ups/run", headers=h(ADMIN)))
    kinds = {i["kind"] for i in ok(client.get("/follow-ups", headers=h(REC)))}
    assert "start_date" in kinds

    # The KPI dashboard reflects the run (BRD section 12).
    report = ok(client.get("/kpis", headers=h(SALES)))
    k = {x["key"]: x for x in report["kpis"]}
    assert k["client_response_rate"]["value"] == 1.0
    assert k["client_response_rate"]["status"] == "on_track"
    assert k["qualified_per_jd"]["value"] == 3.0
    assert k["candidate_response_rate"]["basis"] == {"replied": 1, "contacted": 3}
    assert k["candidate_response_rate"]["status"] == "on_track"
    assert k["hours_to_first_submission"]["value"] < 24
    assert k["resume_to_interview"]["value"] == 1.0 and k["interview_to_offer"]["value"] == 1.0
    assert report["funnel"]["started"] == 1 and report["funnel"]["companies"] == 2
    ops = ok(client.get("/admin/operations", headers=h(ADMIN)))
    assert ops["approvals_pending"]["total"] == 0 and ops["email_30d"]["failed"] == 0
    # Three candidates is below the BRD's 10 per JD, so S12 flags the job to its recruiter.
    assert ops["alerts"] == ["1 open job has fewer than 10 qualified candidates (S12)",
                             "1 follow-up is overdue"]  # fmt: skip


def test_kpis_without_data_say_so(ctx):
    client, _, _, _ = ctx
    report = ok(client.get("/kpis", params={"days": 7}, headers=h(SALES)))
    k = {x["key"]: x for x in report["kpis"]}
    assert len(k) == 9
    for key in ("client_response_rate", "candidate_response_rate", "hours_to_first_submission",
                "resume_to_interview", "interview_to_offer", "qualified_per_jd"):  # fmt: skip
        assert k[key]["value"] is None and k[key]["status"] == "no_data"
    assert k["hours_to_first_submission"]["target"] == "<24 hours"
    assert client.get("/admin/operations", headers=h(SALES)).status_code == 403


def test_operations_alerts_on_stale_approvals(ctx):
    client, services, _, clock = ctx
    services.workflows.start(
        gate=GateType.CANDIDATE_OUTREACH_EMAIL, subject_id="x", channel=Channel.EMAIL,
        recipient="someone@mail.example", content={"subject": "s", "body": "b"},
        requested_by="agent-8",
    )  # fmt: skip
    assert ok(client.get("/admin/operations", headers=h(ADMIN)))["alerts"] == []
    clock.offset += timedelta(hours=49)
    ops = ok(client.get("/admin/operations", headers=h(ADMIN)))
    assert ops["approvals_pending"]["by_gate"] == {"candidate_outreach_email": 1}
    assert any("48 hours" in a for a in ops["alerts"])


def test_s16_no_submission_until_details_are_complete(ctx):
    client, services, _, clock = ctx
    platform = services.platform
    platform.jobs.add(Job("job-1", "JOB-2026-0200", "client-1", "Quality Engineer",
                          created_at=clock.now))  # fmt: skip
    ok(client.post("/candidates", json={"resumes": [{"resume_text": resume(
        "Ana Duarte", "ana@mail.example", 7), "consent": "granted"}]}, headers=h(REC)), 201)  # fmt: skip
    [ana] = platform.candidates.list()
    platform.engagements.save(Engagement(
        "e-1", "job-1", ana.id, EngagementStatus.DETAILS_REQUESTED, consent_store_at=clock.now,
        consent_submit_at=clock.now, missing=["expected_rate", "visa_status"],
    ))  # fmt: skip
    sub = ok(client.post("/jobs/job-1/submissions", json={"candidate_id": ana.id},
                         headers=h(REC)))  # fmt: skip
    r = client.post(f"/submissions/{sub['id']}/request-approval",
                    json={"right_to_represent": True, "acknowledge_flags": True,
                          "recipient": "hm@client.example"}, headers=h(REC))  # fmt: skip
    assert r.status_code == 409 and "not complete (expected_rate, visa_status)" in r.text


def test_s23_erasure_removes_personal_data_and_blocks_contact(ctx):
    client, services, _, clock = ctx
    platform = services.platform
    platform.jobs.add(Job("job-1", "JOB-2026-0201", "client-1", "Quality Engineer",
                          created_at=clock.now))  # fmt: skip
    ok(client.post("/candidates", json={"resumes": [{"resume_text": resume(
        "Ana Duarte", "ana@mail.example", 7)}]}, headers=h(REC)), 201)  # fmt: skip
    [ana] = platform.candidates.list()
    assert [x.id for x in platform.search.search("candidate", "PFMEA PPAP")] == [ana.id]
    engagement = Engagement("e-1", "job-1", ana.id, EngagementStatus.DETAILS_REQUESTED,
                            token_hash="a" * 64, consent_evidence="ticked")  # fmt: skip
    platform.engagements.save(engagement)

    reason = {"reason": "Candidate emailed asking to delete their data"}
    assert (
        client.post(f"/candidates/{ana.id}/erase", json=reason, headers=h(REC)).status_code == 403
    )
    record = ok(client.post(f"/candidates/{ana.id}/erase", json=reason, headers=h(ADMIN)))
    assert record["engagements"] == 1

    erased = platform.candidates.get(ana.id)
    assert erased.full_name == "Erased candidate" and erased.email is None
    assert erased.resume_text is None and erased.skills == []
    assert erased.consent_status is ConsentStatus.WITHDRAWN
    assert platform.search.search("candidate", "PFMEA PPAP") == []
    assert services.opt_outs.is_opted_out("ana@mail.example")
    e = platform.engagements.get("e-1")
    assert e.status is EngagementStatus.DO_NOT_CONTACT and e.token_hash is None
    assert e.consent_evidence == "erased"
    # Erasing twice is refused.
    r = client.post(f"/candidates/{ana.id}/erase", json=reason, headers=h(ADMIN))
    assert r.status_code == 409


def test_s26_refused_requests_are_logged(ctx):
    client, _, _, _ = ctx
    assert client.get("/admin/operations", headers=h(BD)).status_code == 403
    assert (
        client.get("/kpis", headers={"X-Dev-User-Email": "stranger@x.example"}).status_code == 403
    )
    assert client.get("/kpis").status_code == 401
    log = ok(client.get("/admin/access-denied", headers=h(ADMIN)))
    assert [(e["status"], e["path"], e["role"]) for e in log[:3]] == [
        (401, "/kpis", None),
        (403, "/kpis", None),
        (403, "/admin/operations", "bd_manager"),
    ]
    assert log[2]["user_id"] == BD.user_id
