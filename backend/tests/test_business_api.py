"""End-to-end: Phase 2-3 flow through the HTTP API, in memory, with the real LangGraph workflow."""

from urllib.parse import parse_qs, urlsplit

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
from recruiting.domain import Channel, Role  # noqa: E402
from recruiting.identity import CurrentUser, DevHeaderAuthenticator, InMemoryUserDirectory  # noqa: E402
from recruiting.outreach import DeliveryResult, DeliveryStatus, OutreachService  # noqa: E402
from recruiting.workflows.outreach_graph import OutreachWorkflow, build_outreach_graph  # noqa: E402

ADMIN = CurrentUser("u-admin", "admin@cbs.example", "Admin", Role.ADMINISTRATOR)
BD = CurrentUser("u-bd", "bd@cbs.example", "BD", Role.BD_MANAGER)
REC = CurrentUser("u-rec", "rec@cbs.example", "Recruiter", Role.RECRUITER)
AM = CurrentUser("u-am", "am@cbs.example", "Account Manager", Role.ACCOUNT_MANAGER)
SECRET = "hook-secret-123"

CSV = """company,domain,industry,revenue_usd,country,location,career_page_url,contact_name,contact_title,contact_email
Northfield Axle Systems,northfield-axle.example,Automotive,1.2B,US,"Troy, MI",https://northfield-axle.example/careers,Rachel Lee,Engineering Manager,r.lee@northfield-axle.example
Other Corp,other.example,Automotive,2B,US,"Flint, MI",,Kim Wu,HR Manager,kim@other.example
"""
CAREERS = """<script type="application/ld+json">[
{"@type":"JobPosting","title":"CAE Engineer","url":"https://northfield-axle.example/j/1","datePosted":"2026-11-20"},
{"@type":"JobPosting","title":"Quality Engineer","url":"https://northfield-axle.example/j/2","datePosted":"2026-11-19"},
{"@type":"JobPosting","title":"Mechanical Engineer","url":"https://northfield-axle.example/j/3","datePosted":"2026-11-18"}
]</script>"""


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


def as_user(user):
    return {"X-Dev-User-Email": user.email}


@pytest.fixture
def ctx():
    settings = Settings(llm_provider="none", inbound_webhook_secret=SECRET, _env_file=None)
    users = InMemoryUserDirectory([ADMIN, BD, REC, AM])
    approvals, opt_outs, email = ApprovalEngine(), OptOutRegistry(), FakeEmail()
    outreach = OutreachService(approvals, opt_outs, [email])
    workflows = OutreachWorkflow(build_outreach_graph(approvals, outreach, MemorySaver()))
    platform = build_platform(
        settings, users=users, opt_outs=opt_outs, deliveries=outreach.log, workflows=workflows,
        fetcher=Fetcher(),
    )  # fmt: skip
    services = Services(
        approvals, opt_outs, users, DevHeaderAuthenticator(), workflows, outreach, platform
    )
    return TestClient(create_app(services)), services, email


def test_full_client_acquisition_and_jd_intake_flow(ctx):
    client, services, email = ctx

    imported = client.post(
        "/companies/import", json={"source_name": "list-1", "csv": CSV}, headers=as_user(BD)
    )
    assert imported.status_code == 200 and imported.json()["created"] == 2
    companies = client.get("/companies", headers=as_user(BD)).json()
    northfield = next(c for c in companies if c["domain"] == "northfield-axle.example")

    signals = client.post("/companies/hiring-signals", json={}, headers=as_user(BD)).json()
    assert signals["target_postings"] == 3 and signals["scores"][northfield["id"]] >= 70

    drafts = client.post("/companies/outreach-drafts", json={}, headers=as_user(BD)).json()
    # Only companies above the priority threshold are considered; Other Corp has no signal.
    assert len(drafts["drafted"]) == 1 and drafts["skipped"] == {}
    request_id = drafts["drafted"][0]["approval_request_id"]

    queue = client.get("/approvals/pending", headers=as_user(BD)).json()
    assert [r["id"] for r in queue] == [request_id] and email.sent == []
    decision = client.post(
        f"/approvals/{request_id}/decision", json={"action": "approve"}, headers=as_user(BD)
    )
    assert decision.status_code == 200
    [sent] = email.sent  # the workflow resumed after the decision and sent the approved email
    assert sent.recipient == "r.lee@northfield-axle.example"

    reply = {
        "from_address": "r.lee@northfield-axle.example",
        "subject": "Re: " + sent.content["subject"],
        "body": "Thanks - JD attached.",
        "attachments_text": "Job description: CAE Engineer. Required: LS-DYNA, ANSA. Location: Troy, MI",
        "message_id": "<reply-1@mail>",
    }
    assert client.post("/inbound/email", json=reply).status_code == 401
    intake = client.post(
        "/inbound/email", json=reply, headers={"X-RAP-Webhook-Secret": SECRET}
    ).json()
    assert intake["category"] == "jd_received" and intake["approval_request_id"] == request_id
    [job_id] = intake["job_ids"]
    assert services.outreach.receipt(request_id).replied_at is not None

    job = client.get(f"/jobs/{job_id}", headers=as_user(AM)).json()
    assert job["job_code"].startswith("JOB-") and job["recruiter_id"] == "u-rec"
    assert "LS-DYNA" in job["analysis"]["must_have_skills"]
    assert "title" in job["review_fields"]

    patched = client.patch(
        f"/jobs/{job_id}",
        json={"title": "CAE Engineer - Crash", "location": "Troy, MI", "confirm_fields": ["title"]},
        headers=as_user(AM),
    ).json()
    assert patched["title"] == "CAE Engineer - Crash"
    assert "title" not in patched["review_fields"] and "location" not in patched["review_fields"]
    assert (
        client.patch(f"/jobs/{job_id}", json={"title": "x"}, headers=as_user(BD)).status_code == 403
    )

    hits = client.get("/jobs/search", params={"q": "LS-DYNA crash"}, headers=as_user(REC)).json()
    assert hits[0]["job_id"] == job_id

    # The client sees only their own company's jobs, without internal fields.
    client_user = CurrentUser(
        "u-cl", "r.lee@northfield-axle.example", "Rachel Lee", Role.CLIENT, northfield["id"]
    )
    other_client = CurrentUser("u-cl2", "kim@other.example", "Kim", Role.CLIENT, "someone-else")
    services.users = InMemoryUserDirectory([ADMIN, BD, REC, AM, client_user, other_client])
    own = client.get("/jobs", headers=as_user(client_user)).json()
    assert [j["id"] for j in own] == [job_id] and "analysis" not in own[0]
    assert client.get("/jobs", headers=as_user(other_client)).json() == []
    assert client.get(f"/jobs/{job_id}", headers=as_user(other_client)).status_code == 404
    assert client.get("/companies", headers=as_user(client_user)).status_code == 403


def test_roles_and_unsubscribe(ctx):
    client, services, _ = ctx
    client.post("/companies/import", json={"source_name": "l", "csv": CSV}, headers=as_user(BD))
    assert (
        client.post(
            "/companies/import", json={"source_name": "l", "csv": CSV}, headers=as_user(REC)
        ).status_code
        == 403
    )
    assert client.get("/companies", headers=as_user(AM)).status_code == 200

    url = services.platform.unsubscribe.url("Kim@Other.example")
    token = parse_qs(urlsplit(url).query)["token"][0]
    page = client.get("/unsubscribe", params={"token": token})
    assert page.status_code == 200 and "Unsubscribe" in page.text
    assert not services.opt_outs.is_opted_out("kim@other.example")  # GET alone does nothing
    done = client.post("/unsubscribe", data={"token": token})
    assert done.status_code == 200 and services.opt_outs.is_opted_out("kim@other.example")
    other = next(c for c in services.platform.companies.list() if c.domain == "other.example")
    assert other.outreach_status == "do_not_contact"
    assert client.post("/unsubscribe", params={"token": token[:-3] + "AAA"}).status_code == 400


def test_llm_usage_is_admin_only(ctx):
    client, _, _ = ctx
    assert client.get("/admin/llm-usage", headers=as_user(BD)).status_code == 403
    assert client.get("/admin/llm-usage", headers=as_user(ADMIN)).json() == []
