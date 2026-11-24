"""Phase 8 exception paths not covered by earlier phases: S6 (email bounce) and S12 (too few
qualified candidates)."""

import pytest

pytest.importorskip("langgraph")

from test_phase8_end_to_end import (  # noqa: E402
    ADMIN,
    AM,
    BD,
    CSV,
    HOOK,
    JD,
    REC,
    approve,
    build,
    h,
    ok,
)

from recruiting.agents.candidate_search import (  # noqa: E402
    ExternalResume,
    title_variants,
    widened_location,
)
from recruiting.records.models import Contact, ContactRole, Job  # noqa: E402

RACHEL = "r.lee@northfield-axle.example"


@pytest.fixture
def ctx():
    return build()


def first_email_sent(client, services, email, with_alternative=True):
    ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV}, headers=h(BD)))
    companies = services.platform.companies
    company_id = companies.contact_by_email(RACHEL).company_id
    if with_alternative:
        companies.save_contact(Contact("c-alt", company_id, "Sam Ortiz", "HR Manager",
                                       ContactRole.HR_MANAGER, email="s.ortiz@northfield-axle.example"))  # fmt: skip
    ok(client.post("/companies/hiring-signals", json={}, headers=h(BD)))
    [first] = ok(client.post("/companies/outreach-drafts", json={}, headers=h(BD)))["drafted"]
    approve(client, first["approval_request_id"], BD)
    assert [m.recipient for m in email.sent] == [RACHEL]
    return company_id, first["approval_request_id"]


def test_s6_hard_bounce_invalidates_contact_and_suggests_another(ctx):
    client, services, email, _ = ctx
    company_id, approval_id = first_email_sent(client, services, email)
    bounce = {"recipient": RACHEL.upper(), "reason": "550 5.1.1 user unknown"}

    result = ok(client.post("/inbound/bounce", json=bounce, headers=HOOK))
    assert result["action"] == "contact_invalidated"
    assert result["approval_request_id"] == approval_id
    assert result["alternative_contact"]["email"] == "s.ortiz@northfield-axle.example"
    platform = services.platform
    assert platform.deliveries.get(approval_id).status == "bounced"
    assert platform.deliveries.get(approval_id).error == "550 5.1.1 user unknown"
    assert platform.companies.contact_by_email(RACHEL).email_valid is False
    assert platform.companies.get(company_id).outreach_status == "not_contacted"

    # Agent 12 does not chase an email that never arrived.
    ok(client.post("/follow-ups/run", headers=h(ADMIN)))
    kinds = {i["kind"] for i in ok(client.get("/follow-ups", headers=h(ADMIN)))}
    assert "client_outreach" not in kinds

    # Agent 3 drafts to the alternative contact; it waits for approval, nothing is sent.
    [again] = ok(client.post("/companies/outreach-drafts", json={}, headers=h(BD)))["drafted"]
    assert again["contact_id"] == "c-alt"
    assert len(email.sent) == 1

    # A repeat notice changes nothing; the operations view raises the bounce-rate alert.
    ok(client.post("/inbound/bounce", json=bounce, headers=HOOK))
    ops = ok(client.get("/admin/operations", headers=h(ADMIN)))
    assert ops["email_30d"]["bounced"] == 1 and ops["email_30d"]["bounce_rate"] == 1.0
    assert any("bounce rate" in a for a in ops["alerts"])
    report = ok(client.get("/kpis", headers=h(ADMIN)))
    k = {x["key"]: x for x in report["kpis"]}
    assert k["outreach_emails"]["basis"] == {"emails": 0}  # bounced mail is not "sent"


def test_s6_no_alternative_contact_is_reported(ctx):
    client, services, email, _ = ctx
    first_email_sent(client, services, email, with_alternative=False)
    result = ok(client.post("/inbound/bounce", json={"recipient": RACHEL}, headers=HOOK))
    assert result["alternative_contact"] is None
    assert result["no_alternative_reason"] == "no_valid_contact_email"
    drafts = ok(client.post("/companies/outreach-drafts", json={}, headers=h(BD)))
    assert drafts["drafted"] == []


def test_s6_soft_bounce_and_bad_requests(ctx):
    client, services, email, _ = ctx
    _, approval_id = first_email_sent(client, services, email)
    soft = ok(client.post("/inbound/bounce", json={"recipient": RACHEL, "hard": False},
                          headers=HOOK))  # fmt: skip
    assert soft["action"] == "soft_bounce_ignored"
    assert services.platform.deliveries.get(approval_id).status == "sent"
    assert services.platform.companies.contact_by_email(RACHEL).email_valid is True

    assert client.post("/inbound/bounce", json={"recipient": RACHEL}).status_code == 401
    wrong = {"recipient": "someone@else.example", "approval_request_id": approval_id}
    assert client.post("/inbound/bounce", json=wrong, headers=HOOK).status_code == 404


# ----- S12: fewer than 10 qualified candidates ----------


def qe_resume(i, title="Quality Engineer"):
    return (
        f"Person{i} Tester{i}\nTroy, MI | person{i}@mail.example | (248) 555-{1000 + i}\n"
        f"{title} with 7 years of experience in PFMEA, PPAP, APQP and 8D.\n"
        "B.S. Mechanical Engineering"
    )


class RecordingSource:
    """A licensed job board that only finds people when searched by a related title."""

    name = "board"

    def __init__(self):
        self.calls = []

    def search(self, query, skills, location, limit):
        self.calls.append((query.splitlines()[0], location))
        if query.startswith("Supplier Quality Engineer"):
            return [ExternalResume("board", qe_resume(i, "Supplier Quality Engineer"))
                    for i in range(10, 18)]  # fmt: skip
        return []


def open_job(client, email):
    first_email_sent(client, *email)
    reply = {"from_address": RACHEL, "subject": "Re: roles", "body": "JD attached.",
             "attachments_text": JD, "message_id": "<jd-s12@mail>"}  # fmt: skip
    [job_id] = ok(client.post("/inbound/email", json=reply, headers=HOOK))["job_ids"]
    # The rule-based parser leaves location to the Account Manager (S11).
    ok(client.patch(f"/jobs/{job_id}", json={"title": "Quality Engineer", "location": "Troy, MI",
                    "confirm_fields": ["title", "location"]}, headers=h(AM)))  # fmt: skip
    return job_id


def test_s12_shortfall_alerts_recruiter_and_widened_search_fills_it(ctx):
    client, services, email, _ = ctx
    source = RecordingSource()
    services.platform.candidate_search._sources = [source]
    job_id = open_job(client, (services, email))
    ok(client.post("/candidates", json={"resumes": [
        {"resume_text": qe_resume(i), "source": "ats"} for i in range(3)]},
        headers=h(REC)), 201)  # fmt: skip

    ranking = ok(client.post(f"/jobs/{job_id}/ranking", headers=h(REC)))
    gap = ranking["shortfall"]
    assert gap["qualified"] == 3 and gap["target"] == 10 and gap["widened"] is False
    assert "Supplier Quality Engineer" in gap["suggestions"]["title_variants"]
    assert gap["suggestions"]["location"] == ["state", "anywhere"]
    assert source.calls == [("Quality Engineer", "Troy, MI")]  # nothing widened on its own

    # Recruiter alerted: an Agent 12 task in their list, and an operations alert.
    ok(client.post("/follow-ups/run", headers=h(ADMIN)))
    [task] = [i for i in ok(client.get("/follow-ups", headers=h(REC)))
              if i["kind"] == "candidate_shortfall"]  # fmt: skip
    assert task["job_id"] == job_id and "Only 3 of 10" in task["note"]
    ops = ok(client.get("/admin/operations", headers=h(ADMIN)))
    assert "1 open job has fewer than 10 qualified candidates (S12)" in ops["alerts"]

    # Widening runs only with what the recruiter chose, and is recorded.
    assert client.post(f"/jobs/{job_id}/ranking/widen", json={},
                       headers=h(REC)).status_code == 422  # fmt: skip
    assert client.post(f"/jobs/{job_id}/ranking/widen", json={"location": "state"},
                       headers=h(BD)).status_code == 403  # fmt: skip
    source.calls.clear()
    widened = ok(client.post(f"/jobs/{job_id}/ranking/widen", headers=h(REC), json={
        "title_variants": ["Supplier Quality Engineer"], "location": "state"}))  # fmt: skip
    assert source.calls == [("Quality Engineer", "MI"), ("Supplier Quality Engineer", "MI")]
    assert widened["widened"]["by"] == REC.user_id and widened["widened"]["before"] == 3
    assert widened["widened"]["title_variants"] == ["Supplier Quality Engineer"]
    assert widened["shortfall"] is None
    assert len([c for c in widened["candidates"] if c["rank"] is not None]) == 11

    # The task closes by itself once the job has enough candidates.
    ok(client.post("/follow-ups/run", headers=h(ADMIN)))
    kinds = {i["kind"] for i in ok(client.get("/follow-ups", headers=h(REC)))}
    assert "candidate_shortfall" not in kinds
    assert ok(client.get(f"/jobs/{job_id}/ranking", headers=h(REC)))["widened"] is not None


def test_s12_recruiter_rejections_count_against_the_target(ctx):
    client, services, email, _ = ctx
    job_id = open_job(client, (services, email))
    ok(client.post("/candidates", json={"resumes": [
        {"resume_text": qe_resume(i), "source": "ats"} for i in range(10)]},
        headers=h(REC)), 201)  # fmt: skip
    ranking = ok(client.post(f"/jobs/{job_id}/ranking", headers=h(REC)))
    assert ranking["shortfall"] is None
    rejected = next(c for c in ranking["candidates"] if c["rank"] is not None)
    ok(client.post(f"/jobs/{job_id}/ranking/{rejected['candidate_id']}/review",
                   json={"verdict": "not_qualified"}, headers=h(REC)))  # fmt: skip
    assert ok(client.get(f"/jobs/{job_id}/ranking", headers=h(REC)))["shortfall"]["qualified"] == 9


def test_title_variants_and_location_scopes():
    assert title_variants("Senior Quality Engineer II") == [
        "Quality Engineer", "Supplier Quality Engineer", "Quality Assurance Engineer"]  # fmt: skip
    assert title_variants("Controls Engineer")[:2] == ["PLC Engineer", "Automation Engineer"]
    job = Job("j", "J-1", "c", "Quality Engineer", location="Troy, MI")
    assert widened_location(job, None) == "Troy, MI"
    assert widened_location(job, "state") == "MI"
    assert widened_location(job, "anywhere") is None
