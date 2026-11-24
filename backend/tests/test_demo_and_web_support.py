"""Support for the web app: GET /me, local demo data, and the fixes the demo flow exposed."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("langgraph")

from recruiting.agents.jd_intake import InboundEmail, extract_by_rules  # noqa: E402
from recruiting.api.app import create_app  # noqa: E402
from recruiting.api.services import build_services  # noqa: E402
from recruiting.config import Settings  # noqa: E402
from recruiting.domain import GateType, Role  # noqa: E402
from recruiting.records.models import EngagementStatus  # noqa: E402

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def demo():
    services = build_services(Settings(demo_data=True, _env_file=None))
    return services, TestClient(create_app(services))


def h(email):
    return {"X-Dev-User-Email": email}


def test_me_returns_the_signed_in_user(demo):
    _, client = demo
    me = client.get("/me", headers=h("recruiter@cbs.example")).json()
    assert me["role"] == "recruiter" and me["email"] == "recruiter@cbs.example"
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers=h("nobody@x.example")).status_code == 403


def test_demo_data_leaves_work_waiting_for_every_role(demo):
    services, client = demo
    gates = lambda role: sorted(r.gate for r in services.approvals.pending_for(role))  # noqa: E731
    assert gates(Role.BD_MANAGER) == [GateType.FIRST_CLIENT_EMAIL]
    assert gates(Role.RECRUITER) == sorted(
        [GateType.CANDIDATE_OUTREACH_EMAIL] * 2 + [GateType.RESUME_SUBMISSION]
    )
    jobs = {j.title: j for j in services.platform.jobs.list()}
    assert set(jobs) == {"Quality Engineer", "Controls Engineer"}
    assert "title" in jobs["Controls Engineer"].review_fields  # waits for the Account Manager
    assert "title" not in jobs["Quality Engineer"].review_fields
    # The client portal shows nothing until a submission is approved and sent.
    portal = client.get("/client/submissions", headers=h("r.lee@northfield-axle.example"))
    assert portal.status_code == 200 and portal.json() == []


def test_reply_is_read_before_anyone_opens_the_job(demo):
    """Regression: an approved, delivered outreach stayed 'drafted' until someone listed the
    job's engagements, so an 'interested' reply arriving first was not turned into a details
    request. The demo seed replies without listing them first."""
    services, _ = demo
    statuses = {e.status for e in services.platform.engagements.list()}
    assert EngagementStatus.COMPLETE in statuses  # details requested, approved and filled in


def test_demo_has_a_linkedin_note_to_send_by_hand(demo):
    _, client = demo
    [task] = client.get("/linkedin-tasks", headers=h("recruiter@cbs.example")).json()
    assert task["candidate_name"] == "Elena Ruiz"
    assert task["profile_url"].endswith("elena-ruiz-quality")


def test_demo_seed_works_when_the_api_uses_oidc():
    """The seed signs in through its own in-process app, so OIDC deployments can use it."""
    settings = Settings(
        demo_data=True,
        auth_provider="oidc",
        oidc_issuer="https://idp.example",
        oidc_audience="rap-web",
        oidc_jwks_url="https://idp.example/jwks",
        _env_file=None,
    )
    services = build_services(settings)
    assert len(services.platform.jobs.list()) == 2
    client = TestClient(create_app(services))
    # The real app still refuses dev headers: only bearer tokens are accepted.
    assert client.get("/me", headers=h("recruiter@cbs.example")).status_code == 401


def test_demo_data_only_for_local_memory_runs():
    with pytest.raises(RuntimeError, match="only for local in-memory runs"):
        build_services(Settings(demo_data=True, environment="dev", _env_file=None))


def test_rule_title_prefers_the_labelled_title_in_the_attachment():
    email = InboundEmail(
        "o.haddad@client.example",
        "Re: Engineers for Great Lakes Controls",
        "Good timing. JD attached for our Controls Engineer opening and the Manager role.",
        NOW,
        "<m-1@x>",
        "Job description: Controls Engineer. Required: PLC programming, HMI.",
    )
    [job] = extract_by_rules(email).jobs
    assert job.title == "Controls Engineer"
    assert "title" in job.low_confidence_fields  # still for the Account Manager to confirm
    unlabelled = InboundEmail("a@b.example", "Re: hi", "See attached.", NOW, "<m-2@x>",
                              "Senior Quality Engineer\nPFMEA, PPAP")  # fmt: skip
    assert extract_by_rules(unlabelled).jobs[0].title == "Senior Quality Engineer"
