"""Phase 6: Agent 10 matching, the recruiter approval step and Agent 11 client submission."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from recruiting.agents.client_submission import PACKAGE_ITEMS  # noqa: E402
from recruiting.ai.llm import ScriptedProvider  # noqa: E402
from recruiting.api.app import create_app  # noqa: E402
from recruiting.api.platform import build_platform  # noqa: E402
from recruiting.api.services import Services  # noqa: E402
from recruiting.approvals import ApprovalEngine  # noqa: E402
from recruiting.compliance import OptOutRegistry  # noqa: E402
from recruiting.config import Settings  # noqa: E402
from recruiting.domain import Channel, Role  # noqa: E402
from recruiting.identity import CurrentUser, DevHeaderAuthenticator, InMemoryUserDirectory  # noqa: E402
from recruiting.outreach import DeliveryResult, DeliveryStatus, OutreachService  # noqa: E402
from recruiting.records.models import (  # noqa: E402
    Candidate,
    Company,
    ConsentStatus,
    Contact,
    ContactRole,
    Engagement,
    EngagementStatus,
    Job,
)

REC = CurrentUser("u-rec", "rec@cbs.example", "Dana Okafor", Role.RECRUITER)
AM = CurrentUser("u-am", "am@cbs.example", "Account Manager", Role.ACCOUNT_MANAGER)
CLIENT = CurrentUser("u-cl", "hm@northfield.example", "Hiring Manager", Role.CLIENT, "client-1")
OTHER_CLIENT = CurrentUser("u-cl2", "x@other.example", "Other", Role.CLIENT, "client-2")
JD_AT = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)

JOB = Job(
    "job-1", "JOB-2026-0142", "client-1", "CAE Engineer - Crash", location="Troy, MI",
    industry="Automotive", visa_requirements="US citizen or green card holder",
    responsibilities="Full-vehicle crash simulation with LS-DYNA; meshing in ANSA.",
    analysis={"must_have_skills": ["LS-DYNA", "ANSA"], "nice_to_have_skills": ["HyperMesh"],
              "years_experience_min": 5},
    created_at=JD_AT,
)  # fmt: skip
JOB_2 = Job("job-2", "JOB-2026-0150", "client-1", "CAE Engineer - NVH", location="Troy, MI",
            analysis={"must_have_skills": ["Nastran"]}, created_at=JD_AT)  # fmt: skip
RESUME = (
    "Priya Natarajan - CAE Engineer\nSkills: LS-DYNA, ANSA, HyperMesh\n"
    "Lead crash engineer at Lakeside Automotive: full-vehicle frontal and side crash "
    "simulation in LS-DYNA, meshing in ANSA, FMVSS 208 and IIHS load cases."
)


def candidate(cid, name, **kw):
    base = dict(
        email=f"{cid}@mail.example", skills=["LS-DYNA", "ANSA", "HyperMesh"],
        experience_years=8, current_location="Troy, MI", availability="2 weeks",
        visa_status="Green card holder", work_authorization="Green card holder",
        expected_rate="$80/hr C2C", notice_period="2 weeks", preferred_location="Detroit area",
        resume_text=RESUME, consent_status=ConsentStatus.GRANTED,
        work_history=[{"title": "CAE Engineer", "company": "Lakeside Automotive",
                       "start_year": 2019, "end_year": None}],
        projects=[{"title": "Frontal crash", "description": "LS-DYNA full-vehicle crash model"},
                  {"title": "Seat bracket", "description": "Sheet-metal bracket drawing"}],
    )  # fmt: skip
    base.update(kw)
    return Candidate(cid, name, "ats", **base)


def complete_engagement(job_id, cid, consent=True):
    now = datetime(2026, 11, 21, tzinfo=UTC)
    return Engagement(
        f"e-{job_id}-{cid}", job_id, cid, EngagementStatus.COMPLETE,
        consent_store_at=now if consent else None, consent_submit_at=now if consent else None,
    )  # fmt: skip


class FakeEmail:
    channel = Channel.EMAIL

    def __init__(self):
        self.sent = []

    def deliver(self, message):
        self.sent.append(message)
        return DeliveryResult(DeliveryStatus.SENT, f"m-{len(self.sent)}")


def build(llm_provider=None):
    settings = Settings(llm_provider="none", _env_file=None)
    users = InMemoryUserDirectory([REC, AM, CLIENT, OTHER_CLIENT])
    approvals, opt_outs, email = ApprovalEngine(), OptOutRegistry(), FakeEmail()
    outreach = OutreachService(approvals, opt_outs, [email])
    from recruiting.workflows.outreach_graph import OutreachWorkflow, build_outreach_graph

    workflows = OutreachWorkflow(build_outreach_graph(approvals, outreach, MemorySaver()))
    platform = build_platform(settings, users=users, opt_outs=opt_outs, deliveries=outreach.log,
                              workflows=workflows, approvals=approvals,
                              llm_provider=llm_provider)  # fmt: skip
    platform.companies.save(Company("client-1", "Northfield Axle", "northfield.example", "manual",
                                    is_client=True))  # fmt: skip
    platform.companies.save_contact(Contact("ct-hr", "client-1", "Pat HR", "HR", ContactRole.HR_MANAGER,
                                            email="hr@northfield.example"))  # fmt: skip
    platform.companies.save_contact(Contact("ct-hm", "client-1", "Sam Lee", "Crash Lead",
                                            ContactRole.HIRING_MANAGER,
                                            email="hm@northfield.example"))  # fmt: skip
    platform.jobs.add(JOB)
    platform.jobs.add(JOB_2)
    platform.candidates.add(candidate("c-priya", "Priya Natarajan"))
    platform.candidates.add(
        candidate(
            "c-raj",
            "Raj Iyer",
            visa_status="H-1B",
            work_authorization="H-1B transfer",
            skills=["LS-DYNA"],
        )  # fmt: skip
    )
    platform.candidates.add(candidate("c-noconsent", "No Consent"))
    for job_id, cid, consent in [
        ("job-1", "c-priya", True), ("job-2", "c-priya", True), ("job-1", "c-raj", True),
        ("job-1", "c-noconsent", False),
    ]:  # fmt: skip
        platform.engagements.save(complete_engagement(job_id, cid, consent))
    services = Services(
        approvals, opt_outs, users, DevHeaderAuthenticator(), workflows, outreach, platform
    )
    return TestClient(create_app(services)), services, email


@pytest.fixture
def ctx():
    return build()


def h(user):
    return {"X-Dev-User-Email": user.email}


def prepare(client, cid, job="job-1"):
    r = client.post(f"/jobs/{job}/submissions", json={"candidate_id": cid}, headers=h(REC))
    assert r.status_code == 200, r.text
    return r.json()


def request_approval(client, sid, **body):
    return client.post(
        f"/submissions/{sid}/request-approval",
        json={"right_to_represent": True, **body},
        headers=h(REC),
    )


def decide(client, approval_id, action="approve", reason=None):
    r = client.post(
        f"/approvals/{approval_id}/decision",
        json={"action": action, "reason": reason},
        headers=h(REC),
    )
    assert r.status_code == 200, r.text


def test_match_report_and_package_have_every_brd_item(ctx):
    client, _, _ = ctx
    sub = prepare(client, "c-priya")
    match = sub["match"]
    assert sub["status"] == "matched"
    assert 70 <= match["score"] <= 100
    assert match["missing_skills"] == [] and match["flags"] == []
    assert 0 < match["interview_probability"] <= 0.8
    assert match["strengths"] and match["notes"] and match["source"] == "rules"
    assert match["key_projects"][0].startswith("Frontal crash")
    assert set(PACKAGE_ITEMS) <= set(sub["package"])
    assert sub["package"]["match_percent"] == match["score"]
    assert sub["package"]["rate"] == "$80/hr C2C"
    # The hiring manager is preferred over HR as the recipient.
    assert sub["recipient"] == "hm@northfield.example"


def test_full_flow_sends_only_after_approval_and_keeps_the_sent_version(ctx):
    client, services, email = ctx
    sid = prepare(client, "c-priya")["id"]
    r = client.patch(f"/submissions/{sid}", json={"notes": "Led two FMVSS 208 programmes."},
                     headers=h(REC))  # fmt: skip
    assert r.status_code == 200 and r.json()["package"]["notes"].startswith("Led two")

    r = request_approval(client, sid, right_to_represent=False)
    assert r.status_code == 409 and "represented" in r.json()["detail"]
    r = request_approval(client, sid)
    assert r.status_code == 200, r.text
    sub = r.json()
    assert sub["status"] == "pending_approval" and email.sent == []

    pending = client.get("/approvals/pending", headers=h(REC)).json()
    gate = next(p for p in pending if p["id"] == sub["approval_request_id"])
    assert gate["gate"] == "resume_submission"
    assert json.loads(gate["content"]["package"])["match_percent"] == sub["match"]["score"]

    decide(client, sub["approval_request_id"])
    assert len(email.sent) == 1
    message = email.sent[0]
    assert message.recipient == "hm@northfield.example"
    assert "Led two FMVSS 208 programmes." in message.content["body"]
    assert "SKILL MATRIX" in message.content["body"] and RESUME in message.content["body"]

    sub = client.get(f"/submissions/{sid}", headers=h(AM)).json()
    assert sub["status"] == "submitted"
    assert sub["sent_content"] == dict(message.content)
    assert sub["follow_up_at"] is not None  # Agent 12 picks this up
    engagement = services.platform.engagements.find("job-1", "c-priya")
    assert engagement.status is EngagementStatus.SUBMITTED

    summary = client.get("/jobs/job-1/submissions", headers=h(AM)).json()["summary"]
    assert summary["submitted"] == 1
    assert summary["approved_with_consent_rate"] == 1.0
    sent_at = datetime.fromisoformat(sub["sent_at"])
    assert summary["hours_to_first_submission"] == round(
        (sent_at - JD_AT).total_seconds() / 3600, 1
    )


def test_same_candidate_cannot_be_submitted_twice_to_one_client(ctx):
    """S18."""
    client, _, _ = ctx
    sid = prepare(client, "c-priya")["id"]
    assert request_approval(client, sid).status_code == 200
    r = client.post("/jobs/job-2/submissions", json={"candidate_id": "c-priya"}, headers=h(REC))
    assert r.status_code == 409 and "already submitted" in r.json()["detail"]


def test_rejection_sends_nothing_and_keeps_the_reason(ctx):
    """S19."""
    client, _, email = ctx
    sid = prepare(client, "c-priya")["id"]
    aid = request_approval(client, sid).json()["approval_request_id"]
    decide(client, aid, "reject", "Rate too high for this client")
    sub = client.get(f"/submissions/{sid}", headers=h(REC)).json()
    assert (
        sub["status"] == "rejected" and sub["rejection_reason"] == "Rate too high for this client"
    )
    assert email.sent == []
    # After a rejection the candidate is free again for the same client.
    assert client.post("/jobs/job-2/submissions", json={"candidate_id": "c-priya"},
                       headers=h(REC)).status_code == 200  # fmt: skip


def test_flags_need_acknowledging_and_are_shown_to_the_approver(ctx):
    """S17: missing must-have and work authorisation are flagged, not dropped."""
    client, services, _ = ctx
    sub = prepare(client, "c-raj")
    codes = {f["code"] for f in sub["match"]["flags"]}
    assert {"missing_must_have", "work_authorization"} <= codes
    assert sub["match"]["missing_skills"] == ["ANSA"]
    assert sub["match"]["interview_probability"] < 0.2

    r = request_approval(client, sub["id"])
    assert r.status_code == 409 and "Acknowledge" in r.json()["detail"]
    r = request_approval(client, sub["id"], acknowledge_flags=True, other_agency="TalentCo")
    assert r.status_code == 200
    content = services.approvals.get(r.json()["approval_request_id"]).content
    assert "TalentCo" in content["warnings"] and "visa sponsorship" in content["warnings"]


def test_consent_for_this_job_is_required(ctx):
    """Compliance checklist P2."""
    client, _, _ = ctx
    sid = prepare(client, "c-noconsent")["id"]
    r = request_approval(client, sid)
    assert r.status_code == 409 and "consent" in r.json()["detail"]


def test_client_portal_sees_only_its_own_sent_submissions(ctx):
    client, _, _ = ctx
    sid = prepare(client, "c-priya")["id"]
    pending_id = prepare(client, "c-raj")["id"]
    aid = request_approval(client, sid).json()["approval_request_id"]
    request_approval(client, pending_id, acknowledge_flags=True)
    decide(client, aid)

    mine = client.get("/client/submissions", headers=h(CLIENT)).json()
    assert [s["id"] for s in mine] == [sid]  # the pending one is not visible
    assert mine[0]["candidate_name"] == "Priya Natarajan"
    assert set(PACKAGE_ITEMS) <= set(mine[0]["package"])
    assert "match" not in mine[0] and "warnings" not in mine[0]
    assert client.get("/client/submissions", headers=h(OTHER_CLIENT)).json() == []

    r = client.post(f"/client/submissions/{sid}/feedback",
                    json={"decision": "interview", "comment": "Tuesday 10am?"},
                    headers=h(OTHER_CLIENT))  # fmt: skip
    assert r.status_code == 404
    r = client.post(f"/client/submissions/{sid}/feedback",
                    json={"decision": "interview", "comment": "Tuesday 10am?"},
                    headers=h(CLIENT))  # fmt: skip
    assert r.status_code == 200 and r.json()["client_decision"] == "interview"
    summary = client.get("/jobs/job-1/submissions", headers=h(REC)).json()["summary"]
    assert summary["interview_rate"] == 1.0

    # Clients cannot reach staff endpoints.
    assert client.get(f"/submissions/{sid}", headers=h(CLIENT)).status_code == 403
    assert client.get("/jobs/job-1/submissions", headers=h(CLIENT)).status_code == 403


def test_llm_review_keeps_platform_gaps():
    def review(_request):
        return {
            "strengths": ["Eight years of LS-DYNA crash work"],
            "concerns": [],
            "key_projects": ["Frontal crash"],
            "summary": "Experienced crash CAE engineer.",
            "submission_notes": "Strong fit for frontal crash load cases.",
        }

    provider = ScriptedProvider({"match_review": review})
    client, _, _ = build(provider)
    sub = prepare(client, "c-raj")
    match = sub["match"]
    assert match["source"] == "llm"
    assert match["notes"] == "Strong fit for frontal crash load cases."
    assert any("ANSA" in c for c in match["concerns"])  # not lost when the LLM omits it
    assert sub["package"]["candidate_summary"] == "Experienced crash CAE engineer."
    assert "Resume:" in provider.requests[0].user and "ANSA" in provider.requests[0].user


def test_closed_job_and_unknown_ids(ctx):
    client, services, _ = ctx
    job = services.platform.jobs.get("job-2")
    from recruiting.records.models import JobStatus

    job.status = JobStatus.FILLED
    services.platform.jobs.save(job)
    r = client.post("/jobs/job-2/submissions", json={"candidate_id": "c-priya"}, headers=h(REC))
    assert r.status_code == 409
    r = client.post("/jobs/job-x/submissions", json={"candidate_id": "c-priya"}, headers=h(REC))
    assert r.status_code == 404
    assert client.get("/submissions/nope", headers=h(REC)).status_code == 404
    # The Account Manager can review but not send for approval.
    sid = prepare(client, "c-priya")["id"]
    r = client.post(f"/submissions/{sid}/request-approval", json={"right_to_represent": True},
                    headers=h(AM))  # fmt: skip
    assert r.status_code == 403
    assert request_approval(client, sid).status_code == 200
    r = client.post(f"/submissions/{sid}/withdraw", headers=h(REC))
    assert r.status_code == 409  # pending approval: decide it in the queue instead
