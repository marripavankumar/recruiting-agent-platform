"""Phase 7: Agent 12 follow-ups, the placement pipeline and closing a job (S8, S20-S22)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from recruiting.api.app import create_app  # noqa: E402
from recruiting.api.platform import build_platform  # noqa: E402
from recruiting.api.services import Services  # noqa: E402
from recruiting.approvals import ApprovalEngine, ApprovalStatus, DecisionAction  # noqa: E402
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
    Candidate,
    Company,
    Contact,
    ContactRole,
    Engagement,
    EngagementStatus,
    Job,
    OutreachStatus,
    PlacementStage,
    RankedCandidate,
    Ranking,
    Submission,
    SubmissionStatus,
)
from recruiting.workflows.outreach_graph import OutreachWorkflow, build_outreach_graph  # noqa: E402

ADMIN = CurrentUser("u-admin", "admin@cbs.example", "Admin", Role.ADMINISTRATOR)
BD = CurrentUser("u-bd", "bd@cbs.example", "Marcus Hale", Role.BD_MANAGER)
REC = CurrentUser("u-rec", "rec@cbs.example", "Dana Okafor", Role.RECRUITER)
AM = CurrentUser("u-am", "am@cbs.example", "Leah Kim", Role.ACCOUNT_MANAGER)
CLIENT = CurrentUser("u-cl", "hm@northfield.example", "Rachel Lee", Role.CLIENT, "client-1")
DAY = timedelta(days=1)


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC)

    def __call__(self):
        return self.now

    def advance(self, delta):
        self.now += delta


class FakeEmail:
    channel = Channel.EMAIL

    def __init__(self):
        self.sent = []

    def deliver(self, message):
        self.sent.append(message)
        return DeliveryResult(DeliveryStatus.SENT, f"m-{len(self.sent)}")


@pytest.fixture
def ctx():
    clock = Clock()
    settings = Settings(llm_provider="none", public_base_url="https://rap.example", _env_file=None)
    users = InMemoryUserDirectory([ADMIN, BD, REC, AM, CLIENT])
    approvals, opt_outs, email = ApprovalEngine(), OptOutRegistry(), FakeEmail()
    outreach = OutreachService(approvals, opt_outs, [email, ManualLinkedInChannel()])
    workflows = OutreachWorkflow(build_outreach_graph(approvals, outreach, MemorySaver()))
    platform = build_platform(settings, users=users, opt_outs=opt_outs, deliveries=outreach.log,
                              workflows=workflows, approvals=approvals, clock=clock)  # fmt: skip
    platform.companies.save(Company("client-1", "Northfield Axle", "northfield.example", "list",
                                    outreach_status=OutreachStatus.CONTACTED))  # fmt: skip
    platform.companies.save_contact(Contact("ct-1", "client-1", "Rachel Lee", "Hiring Manager",
                                            ContactRole.HIRING_MANAGER,
                                            email="hm@northfield.example"))  # fmt: skip
    platform.jobs.add(Job("job-1", "JOB-2026-0142", "client-1", "CAE Engineer - Crash",
                          recruiter_id=REC.user_id, created_at=clock.now))  # fmt: skip
    for cid, name in [("c-priya", "Priya Natarajan"), ("c-wei", "Wei Zhang"),
                      ("c-raj", "Raj Iyer")]:  # fmt: skip
        platform.candidates.add(Candidate(cid, name, "ats", email=f"{cid}@mail.example",
                                          linkedin_url=f"https://linkedin.com/in/{cid}"))  # fmt: skip
    services = Services(
        approvals, opt_outs, users, DevHeaderAuthenticator(), workflows, outreach, platform
    )
    return TestClient(create_app(services)), services, email, clock


def h(user):
    return {"X-Dev-User-Email": user.email}


def run(client, user=ADMIN):
    r = client.post("/follow-ups/run", headers=h(user))
    assert r.status_code == 200, r.text
    return r.json()


def approve(client, approval_id, user):
    r = client.post(f"/approvals/{approval_id}/decision", json={"action": "approve"},
                    headers=h(user))  # fmt: skip
    assert r.status_code == 200, r.text


def items(client, user, kind=None):
    params = {"kind": kind} if kind else {}
    return client.get("/follow-ups", params=params, headers=h(user)).json()


def first_client_email(services, client):
    aid = services.workflows.start(
        gate=GateType.FIRST_CLIENT_EMAIL, subject_id="ct-1", channel=Channel.EMAIL,
        recipient="hm@northfield.example",
        content={"subject": "Engineering staffing support", "body": "Dear Ms Lee, ..."},
        requested_by="agent-3",
    )  # fmt: skip
    approve(client, aid, BD)
    return aid


def sent_submission(services, clock, cid="c-priya", name="Priya Natarajan"):
    sub = Submission(
        f"s-{cid}", "job-1", cid, "client-1", status=SubmissionStatus.SUBMITTED,
        recipient="hm@northfield.example", approval_request_id=f"a-{cid}",
        right_to_represent_at=clock.now, sent_at=clock.now,
        sent_content={"subject": f"Candidate submission: JOB-2026-0142 - {name}", "body": "..."},
        package={"candidate_name": name}, stage=PlacementStage.SUBMITTED, stage_at=clock.now,
        created_at=clock.now,
    )  # fmt: skip
    return services.platform.submissions._submissions.save(sub)


def test_client_follow_ups_follow_the_cadence_then_stop(ctx):
    """S8 and Q13: 3 follow-ups at 3, 7 and 14 days, each through the approval gate."""
    client, services, email, clock = ctx
    first_client_email(services, client)
    assert len(email.sent) == 1

    report = run(client)
    assert report["created"] == 1 and report["drafted"] == []
    [item] = items(client, BD, "client_outreach")
    assert item["attempt"] == 0 and item["owner_role"] == "bd_manager"
    due = datetime.fromisoformat(item["due_at"])
    assert due - datetime.fromisoformat(item["anchor_at"]) == 3 * DAY

    clock.advance(3 * DAY + timedelta(hours=1))
    report = run(client)
    [drafted] = report["drafted"]
    request = services.approvals.get(drafted["approval_request_id"])
    assert request.gate is GateType.FIRST_CLIENT_EMAIL and request.requested_by == "agent-12"
    assert request.content["subject"] == "Re: Engineering staffing support"
    assert "unsubscribe" in request.content["body"] and "Marcus" not in request.content["body"]
    assert run(client)["drafted"] == []  # idempotent: not drafted twice
    assert len(email.sent) == 1  # nothing is sent before approval

    clock.advance(4 * DAY)  # day 7, the first follow-up still waits for approval
    report = run(client)
    assert report["drafted"] == [] and report["alerts"] == 1
    approve(client, drafted["approval_request_id"], BD)
    assert len(email.sent) == 2
    assert len(run(client)["drafted"]) == 1  # second follow-up

    clock.advance(7 * DAY)  # day 14: the third and last
    second = items(client, BD, "client_outreach")[0]["approvals"][-1]
    approve(client, second, BD)
    assert len(run(client)["drafted"]) == 1
    assert items(client, BD, "client_outreach") == []  # stopped at the limit
    clock.advance(30 * DAY)
    assert run(client)["drafted"] == []
    assert client.get("/follow-ups/summary", headers=h(BD)).json()["stopped"] == 1


def test_a_reply_closes_the_follow_up(ctx):
    client, services, _, _ = ctx
    first_client_email(services, client)
    run(client)
    services.platform.companies.set_outreach_status("client-1", OutreachStatus.REPLIED)
    assert run(client)["closed"] == 1
    assert items(client, BD) == []


def test_candidate_details_reminders_reuse_agent_9_and_count_from_the_first_request(ctx):
    client, services, _, clock = ctx
    platform = services.platform
    platform.engagements.save(Engagement("e-1", "job-1", "c-wei", EngagementStatus.INTERESTED))
    engagement, _ = platform.resume_collection.request("e-1", "Dana Okafor")
    first_request = engagement.approvals["details_request"]
    run(client)
    [item] = items(client, REC, "candidate_details")
    assert "still owes" in item["note"] and item["owner_id"] == REC.user_id

    clock.advance(3 * DAY + timedelta(hours=1))
    [drafted] = run(client)["drafted"]
    engagement = platform.engagements.get("e-1")
    assert drafted["approval_request_id"] == engagement.approvals["details_request"]
    assert drafted["approval_request_id"] != first_request
    [item] = items(client, REC, "candidate_details")
    # The cadence still counts from the first request: next due on day 7, not day 10.
    assert (
        datetime.fromisoformat(item["due_at"]) - datetime.fromisoformat(item["anchor_at"])
        == 7 * DAY
    )

    engagement.status = EngagementStatus.COMPLETE
    platform.engagements.save(engagement)
    assert run(client)["closed"] == 1


def test_opted_out_candidate_is_not_chased(ctx):
    client, services, _, clock = ctx
    e = Engagement("e-2", "job-1", "c-raj", EngagementStatus.CONTACTED)
    e.log(clock.now, "contacted")
    services.platform.engagements.save(e)
    run(client)
    services.opt_outs.record("c-raj@mail.example", "reply")
    clock.advance(4 * DAY)
    assert run(client)["drafted"] == []
    assert items(client, REC, "candidate_outreach") == []


def test_pipeline_drives_interview_offer_and_start_reminders(ctx):
    client, services, _, clock = ctx
    sid = sent_submission(services, clock).id
    run(client)
    assert [i["kind"] for i in items(client, AM)] == ["submission_feedback"]

    # The client asks for an interview in the portal.
    r = client.post(f"/client/submissions/{sid}/feedback", json={"decision": "interview"},
                    headers=h(CLIENT))  # fmt: skip
    assert r.status_code == 200 and r.json()["stage"] == "interview_requested"
    run(client)
    assert [i["kind"] for i in items(client, REC)] == ["interview_scheduling"]
    assert items(client, AM) == []

    def stage(body, user=REC):
        return client.post(f"/submissions/{sid}/stage", json=body, headers=h(user))

    assert stage({"stage": "offer_accepted"}).status_code == 409  # not the next step
    assert stage({"stage": "interview_scheduled"}).status_code == 409  # needs a time
    interview = clock.now + 2 * DAY
    assert stage({"stage": "interview_scheduled",
                  "interview_at": interview.isoformat()}).status_code == 200  # fmt: skip
    run(client)
    [prep] = items(client, REC)
    assert prep["kind"] == "interview_prep"
    assert datetime.fromisoformat(prep["due_at"]) == interview - DAY

    clock.advance(3 * DAY)  # the interview is over, no outcome yet
    report = run(client)
    assert [d["kind"] for d in report["drafted"]] == ["interview_feedback"]
    request = services.approvals.get(report["drafted"][0]["approval_request_id"])
    assert request.gate is GateType.RESUME_SUBMISSION
    assert "Thank you for interviewing Priya Natarajan" in request.content["body"]

    assert stage({"stage": "interviewed"}).status_code == 200
    assert stage({"stage": "offer_extended"}, AM).status_code == 200
    run(client)
    assert [i["kind"] for i in items(client, AM)] == ["offer"]
    assert [i["kind"] for i in items(client, REC)] == ["offer"]  # both follow the offer

    start = (clock.now + 21 * DAY).date()
    assert stage({"stage": "offer_accepted", "start_date": start.isoformat()}).status_code == 200
    run(client)
    [item] = items(client, REC)
    assert item["kind"] == "start_date"
    assert datetime.fromisoformat(item["due_at"]).date() == start - 7 * DAY
    for _ in range(3):  # documents, first-day check, first-week check-in
        r = client.post(f"/follow-ups/{item['id']}/complete", json={"note": "ok"}, headers=h(REC))
        assert r.status_code == 200
    assert r.json()["status"] == "done"
    summary = client.get("/follow-ups/summary", headers=h(ADMIN)).json()
    assert summary["open_without_due_date"] == 0


def test_client_rejection_and_withdrawal(ctx):
    """S20 (rejected with reason) and S21 (withdrawn; next-ranked candidates suggested)."""
    client, services, _, clock = ctx
    platform = services.platform
    platform.rankings.save(Ranking("job-1", clock.now, {}, [
        RankedCandidate("c-priya", 92, {}, [], [], rank=1),
        RankedCandidate("c-wei", 89, {}, [], [], rank=2),
        RankedCandidate("c-raj", 70, {}, [], [], rank=3),
    ]))  # fmt: skip
    rejected = sent_submission(services, clock).id
    r = client.post(f"/client/submissions/{rejected}/feedback",
                    json={"decision": "rejected", "comment": "Needs more IIHS experience"},
                    headers=h(CLIENT))  # fmt: skip
    assert r.json()["stage"] == "client_rejected"
    sub = client.get(f"/submissions/{rejected}", headers=h(REC)).json()
    assert sub["stage_reason"] == "Needs more IIHS experience"

    withdrawn = sent_submission(services, clock, "c-wei", "Wei Zhang").id
    r = client.post(f"/submissions/{withdrawn}/stage",
                    json={"stage": "candidate_withdrawn"}, headers=h(REC))  # fmt: skip
    assert r.status_code == 409  # a reason is required
    r = client.post(f"/submissions/{withdrawn}/stage",
                    json={"stage": "candidate_withdrawn", "reason": "Took another offer"},
                    headers=h(REC))  # fmt: skip
    assert [s["candidate_id"] for s in r.json()["suggestions"]] == ["c-raj"]
    run(client)
    assert items(client, AM) == []  # nothing left to chase for either


def test_closing_a_job_stops_everything_in_flight(ctx):
    """S22: pending drafts are cancelled, follow-ups stop, nothing is sent."""
    client, services, email, _ = ctx
    r = client.post("/jobs/job-1/outreach",
                    json={"candidate_ids": ["c-priya"], "channels": ["linkedin", "email"]},
                    headers=h(REC))  # fmt: skip
    drafts = [d["approval_request_id"] for d in r.json()["drafted"]]
    assert len(drafts) == 2
    e = Engagement("e-3", "job-1", "c-raj", EngagementStatus.CONTACTED)
    e.log(datetime.now(UTC), "contacted")
    services.platform.engagements.save(e)
    run(client)
    assert items(client, REC, "candidate_outreach")

    r = client.patch("/jobs/job-1", json={"status": "filled"}, headers=h(AM))
    assert r.status_code == 200, r.text
    assert r.json()["stopped"] == {"approvals_cancelled": 2, "follow_ups_cancelled": 1}
    for aid in drafts:
        request = services.approvals.get(aid)
        assert request.status is ApprovalStatus.REJECTED
        assert request.decisions[-1].action is DecisionAction.CANCEL
    engagements = client.get("/jobs/job-1/engagements", headers=h(REC)).json()["engagements"]
    assert {x["status"] for x in engagements if x["candidate_id"] == "c-priya"} == {
        "draft_rejected"
    }
    assert items(client, REC) == [] and email.sent == []
    assert client.get("/approvals/pending", headers=h(REC)).json() == []


def test_cadence_is_set_by_the_administrator(ctx):
    client, services, _, _ = ctx
    assert client.get("/admin/follow-up-cadence", headers=h(BD)).status_code == 403
    cadence = client.get("/admin/follow-up-cadence", headers=h(ADMIN)).json()
    assert cadence["client_outreach"] == [3, 7, 14] and cadence["start_date"] == [-7, -1, 7]
    r = client.put("/admin/follow-up-cadence/client_outreach", json={"days": [7, 2]},
                   headers=h(ADMIN))  # fmt: skip
    assert r.status_code == 422
    r = client.put("/admin/follow-up-cadence/client_outreach", json={"days": [2, 5]},
                   headers=h(ADMIN))  # fmt: skip
    assert r.json() == {"client_outreach": [2, 5]}
    first_client_email(services, client)
    run(client)
    [item] = items(client, BD)
    assert (
        datetime.fromisoformat(item["due_at"]) - datetime.fromisoformat(item["anchor_at"])
        == 2 * DAY
    )


def test_tasks_snooze_and_cancel_is_not_a_reviewer_action(ctx):
    client, services, _, clock = ctx
    sid = sent_submission(services, clock).id
    client.post(f"/client/submissions/{sid}/feedback", json={"decision": "interview"},
                headers=h(CLIENT))  # fmt: skip
    run(client)
    [task] = items(client, REC)
    r = client.post(f"/follow-ups/{task['id']}/draft", headers=h(REC))
    assert r.status_code == 409  # a task, not a message
    r = client.post(f"/follow-ups/{task['id']}/snooze",
                    json={"until": (clock.now + 100 * DAY).isoformat()}, headers=h(REC))  # fmt: skip
    assert r.status_code == 409
    until = clock.now + 5 * DAY
    r = client.post(f"/follow-ups/{task['id']}/snooze", json={"until": until.isoformat()},
                    headers=h(REC))  # fmt: skip
    assert datetime.fromisoformat(r.json()["due_at"]) == until
    assert client.get("/follow-ups", params={"scope": "all"}, headers=h(REC)).status_code == 403
    assert client.get("/follow-ups", headers=h(CLIENT)).status_code == 403

    pending = services.workflows.start(
        gate=GateType.FIRST_CLIENT_EMAIL, subject_id="ct-1", channel=Channel.EMAIL,
        recipient="hm@northfield.example", content={"subject": "s", "body": "b"},
        requested_by="agent-3",
    )  # fmt: skip
    r = client.post(f"/approvals/{pending}/decision", json={"action": "cancel", "reason": "x"},
                    headers=h(BD))  # fmt: skip
    assert r.status_code == 409
