"""Deep agents (LangGraph + deepagents) driving the platform, with a scripted model.

The scripted model stands in for Claude: each reply is the tool call the model would make.
Everything else is real: the deep-agent graphs, subagents via the task tool, the human-in-the-
loop interrupt and resume, the domain tools and their guardrails, the approval gates.
"""

from datetime import timedelta

import pytest

pytest.importorskip("deepagents")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from test_mailbox import FakeMailbox, mail  # noqa: E402
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

from recruiting.agentic.agents import SPECS  # noqa: E402
from recruiting.agentic.runs import AgentRunner, InMemoryAgentRuns, RunStatus  # noqa: E402
from recruiting.agentic.testing import ScriptedChatModel, call, say  # noqa: E402
from recruiting.ai.llm import InMemoryUsageLedger  # noqa: E402
from recruiting.domain import GateType  # noqa: E402
from recruiting.mailbox import InMemoryProcessed, MailboxReader  # noqa: E402
from recruiting.scheduler import default_jobs  # noqa: E402

RACHEL = "r.lee@northfield-axle.example"


def qe_resume(i):
    return (
        f"Person{i} Tester{i}\nTroy, MI | person{i}@mail.example | (248) 555-{1000 + i}\n"
        "Quality Engineer with 7 years of experience in PFMEA, PPAP, APQP and 8D."
    )


def platform_with_job(candidates=3):
    client, services, email, clock = build()
    ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV}, headers=h(BD)))
    ok(client.post("/companies/hiring-signals", json={}, headers=h(BD)))
    [first] = ok(client.post("/companies/outreach-drafts", json={}, headers=h(BD)))["drafted"]
    approve(client, first["approval_request_id"], BD)
    reply = {
        "from_address": RACHEL,
        "subject": "Re: roles",
        "body": "JD attached.",
        "attachments_text": JD,
        "message_id": "<jd@mail>",
    }
    [job_id] = ok(client.post("/inbound/email", json=reply, headers=HOOK))["job_ids"]
    ok(
        client.patch(
            f"/jobs/{job_id}",
            json={
                "title": "Quality Engineer",
                "location": "Troy, MI",
                "confirm_fields": ["title", "location"],
            },
            headers=h(AM),
        )
    )
    if candidates:
        ok(
            client.post(
                "/candidates",
                json={
                    "resumes": [
                        {"resume_text": qe_resume(i), "source": "ats"} for i in range(candidates)
                    ]
                },
                headers=h(REC),
            ),
            201,
        )
    return client, services, email, clock, job_id


def runner_for(services, script, clock):
    model = ScriptedChatModel(script=script)
    ledger = InMemoryUsageLedger()
    runner = AgentRunner(
        services.platform, model, InMemoryAgentRuns(), MemorySaver(), ledger=ledger, clock=clock
    )
    services.agents = runner
    return runner, model, ledger


def pending_approvals(services, gate):
    return [r for r in services.approvals.pending() if r.gate is gate]


def ranked_ids(services, job_id):
    return [c.candidate_id for c in services.platform.rankings.get(job_id).top]


# ----- recruiting coordinator ----------


def test_recruiting_agent_delegates_pauses_for_the_recruiter_and_drafts_for_approval():
    client, services, email, clock, job_id = platform_with_job()
    runner, model, ledger = runner_for(
        services,
        [
            call(
                "write_todos",
                todos=[
                    {"content": "Source", "status": "in_progress"},
                    {"content": "Engage", "status": "pending"},
                ],
            ),
            call(
                "task", "t1", description="Source candidates for the job", subagent_type="sourcer"
            ),
            call("search_and_rank", limit=20),
            call("widen_search", title_variants=["Supplier Quality Engineer"], location="state"),
            say("Ranked 3 candidates; widened the search to Michigan with approval."),
            call(
                "task", "t2", description="Contact the ranked candidates", subagent_type="engager"
            ),
            lambda _m: call(
                "draft_outreach", candidate_ids=ranked_ids(services, job_id), channels=["email"]
            ),
            say("Drafted 3 emails for approval."),
            say(
                "Report: 3 candidates ranked (fewer than 10: search widened); 3 outreach emails "
                "wait for the recruiter's approval."
            ),
        ],
        clock,
    )

    run = runner.start("recruiting", job_id, REC.user_id, recruiter=REC.display_name)
    # The widened search waits for the recruiter (S12); nothing ran yet.
    assert run.status is RunStatus.WAITING
    [action] = run.pending[0]["action_requests"]
    assert action["name"] == "widen_search" and action["args"]["location"] == "state"
    assert services.platform.rankings.get(job_id).widened is None
    assert [a["tool"] for a in run.actions] == ["search_and_rank"]

    run = runner.resume(run.id, [{"type": "approve"}], REC.user_id, REC.display_name)
    assert run.status is RunStatus.DONE, run.error
    assert run.summary.startswith("Report: 3 candidates ranked")
    assert [a["tool"] for a in run.actions] == ["search_and_rank", "widen_search", "draft_outreach"]
    assert services.platform.rankings.get(job_id).widened["by"] == "agent:recruiting"
    drafts = pending_approvals(services, GateType.CANDIDATE_OUTREACH_EMAIL)
    assert len(drafts) == 3 and all(d.requested_by == "agent-8" for d in drafts)
    assert len(email.sent) == 1  # only the first client email: the agent sent nothing
    assert run.decisions[0]["by"] == REC.user_id
    # Cost of each model call is recorded per agent.
    entries = [e for e in ledger.entries() if e.template_key == "agent:recruiting"]
    assert entries and all(e.cost_usd and e.cost_usd > 0 for e in entries)
    # The subagents got only their own tools; nobody can approve anything.
    offered = {name for names in model.offered for name in names}
    assert {"draft_outreach", "widen_search", "search_and_rank"} <= offered
    assert not any("approv" in n or "verdict" in n or "review" in n for n in offered)


def test_rejected_widening_does_not_run():
    _, services, _, clock, job_id = platform_with_job()
    runner, _, _ = runner_for(
        services,
        [
            call("task", "t1", description="Widen the search", subagent_type="sourcer"),
            call("widen_search", location="anywhere"),
            say("The recruiter declined widening; staying with the current ranking."),
            say("Report: widening declined."),
        ],
        clock,
    )
    run = runner.start("recruiting", job_id, REC.user_id)
    assert run.status is RunStatus.WAITING
    run = runner.resume(run.id, [{"type": "reject", "message": "Keep it local"}], REC.user_id)
    assert run.status is RunStatus.DONE and run.actions == []
    assert services.platform.rankings.get(job_id) is None


def test_guardrails_hold_whatever_the_model_asks():
    _, services, _, clock, job_id = platform_with_job()
    other = services.platform.candidates.list()[0].id
    runner, model, _ = runner_for(
        services,
        [
            # The coordinator itself has no tool that changes anything; its subagents do.
            call("draft_outreach", candidate_ids=[other]),
            call("execute", command="cat /etc/passwd"),
            call("task", "t1", description="Contact candidates", subagent_type="engager"),
            call("draft_outreach", candidate_ids=[other]),  # no ranking yet
            call("draft_outreach", candidate_ids=["not-ranked"]),
            call("request_candidate_details", candidate_id=other),  # never contacted
            say("Blocked: no ranking and nobody contacted yet."),
            say("Report: blocked."),
        ],
        clock,
    )
    run = runner.start("recruiting", job_id, REC.user_id)
    assert run.status is RunStatus.DONE
    main = [m.content for m in model.seen[-1] if m.type == "tool"]
    assert "draft_outreach is not a valid tool" in main[0]
    assert "execute is not a valid tool" in main[1]
    engager = [m.content for m in model.seen[-2] if m.type == "tool"]
    assert "No ranking yet" in engager[0] and "No ranking yet" in engager[1]
    assert "has not been contacted" in engager[2]
    assert pending_approvals(services, GateType.CANDIDATE_OUTREACH_EMAIL) == []

    # With a ranking, only ranked candidates can be contacted.
    runner2, model2, _ = runner_for(
        services,
        [
            call("task", "t2", description="Rank then contact", subagent_type="engager"),
            call("draft_outreach", candidate_ids=["not-ranked"]),
            say("Nothing drafted."),
            say("Report."),
        ],
        clock,
    )
    services.platform.rankings.save(
        services.platform.candidate_search.run(services.platform.jobs.get(job_id))[0]
    )
    runner2.start("recruiting", job_id, REC.user_id)
    engager = [m.content for m in model2.seen[-2] if m.type == "tool"]
    assert '"not_in_ranking": ["not-ranked"]' in engager[0]


# ----- intake from the mailbox ----------


def test_intake_agent_files_client_emails_and_falls_back_if_it_fails():
    _, services, _, clock, _ = platform_with_job(candidates=0)
    jobs_before = len(services.platform.jobs.list())
    extracted = {
        "title": "Controls Engineer",
        "responsibilities": "PLC programming",
        "required_skills": ["PLC", "Allen-Bradley"],
        "preferred_skills": ["Python"],
        "location": "Detroit, MI",
        "experience": "5+ years",
        "visa_status": None,
        "education": None,
        "industry": "Automotive",
        "travel": None,
        "work_mode": "onsite",
        "salary": None,
        "contract_type": "Contract",
        "priority": "urgent",
        "low_confidence_fields": ["salary"],
    }
    runner, _, _ = runner_for(
        services,
        [
            call("lookup_sender"),
            call(
                "file_client_reply",
                category="jd_received",
                summary="New Controls Engineer JD",
                jobs=[extracted],
            ),
            say("Filed one Controls Engineer job for the Account Manager to confirm."),
        ],
        clock,
    )
    mailbox = FakeMailbox()
    reader = MailboxReader(
        mailbox, services.platform, InMemoryProcessed(), clock=clock, agents=runner
    )
    mailbox.add(
        mail(
            RACHEL,
            "Another role",
            "We urgently need a Controls Engineer in Detroit.",
            "<cj-1@client>",
        )
    )
    report = reader.run()
    assert report.by_agent == 1 and report.agent_fallbacks == 0
    job = next(j for j in services.platform.jobs.list() if j.title == "Controls Engineer")
    assert job.location == "Detroit, MI" and job.priority.value == "urgent"
    assert "salary" in job.review_fields
    [run] = runner.runs.list(agent="intake")
    assert run.subject_id == "<cj-1@client>" and run.started_by == "mailbox"

    # The model fails (no scripted reply left): the deterministic agents still file it.
    mailbox.add(mail(RACHEL, "Re: roles", "Please unsubscribe me.", "<u-1@client>"))
    report = reader.run()
    assert report.agent_fallbacks == 1 and report.routed == 1
    assert services.opt_outs.is_opted_out(RACHEL)
    assert len(services.platform.jobs.list()) == jobs_before + 1


# ----- business development ----------


def test_business_development_agent_drafts_personal_first_emails():
    client, services, _, clock = build()
    ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV}, headers=h(BD)))
    ok(client.post("/companies/hiring-signals", json={}, headers=h(BD)))
    northfield = services.platform.companies.contact_by_email(RACHEL)
    body = (
        "Hi Rachel,\n\nI saw Northfield Axle is hiring three quality engineers in Troy. CBS "
        "LLC places pre-screened automotive quality engineers (PFMEA, PPAP, APQP) within "
        "days. Would a short call next week help?\n\nBest regards,\nMarcus Hale"
    )
    runner, _, _ = runner_for(
        services,
        [
            call("task", "t1", description="Draft for Northfield", subagent_type="outreach_writer"),
            call("get_company", company_id=northfield.company_id),
            call(
                "draft_first_email",
                company_id=northfield.company_id,
                contact_id=northfield.id,
                subject="Quality engineers for Troy",
                body=body,
            ),
            call(
                "draft_first_email",
                company_id=northfield.company_id,
                contact_id=northfield.id,
                subject="Again",
                body=body,
            ),  # already contacted now: refused
            say("Drafted one email."),
            say("Report: one first email waits for the BD Manager."),
        ],
        clock,
    )
    run = runner.start("business_development", None, BD.user_id)
    assert run.status is RunStatus.DONE
    [draft] = pending_approvals(services, GateType.FIRST_CLIENT_EMAIL)
    assert draft.requested_by == "agent:business_development"
    assert (
        draft.content["body"].startswith("Hi Rachel")
        and "/unsubscribe?token=" in draft.content["body"]
    )  # compliance footer added by the tool
    assert services.platform.companies.get(northfield.company_id).outreach_status == "contacted"
    assert [a["tool"] for a in run.actions] == ["draft_first_email"]


# ----- follow-up ----------


def test_follow_up_agent_drafts_due_messages():
    client, services, _, clock = build()
    ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV}, headers=h(BD)))
    ok(client.post("/companies/hiring-signals", json={}, headers=h(BD)))
    [first] = ok(client.post("/companies/outreach-drafts", json={}, headers=h(BD)))["drafted"]
    approve(client, first["approval_request_id"], BD)
    clock.offset = timedelta(days=4)  # no reply for 4 days: the first follow-up is due

    def draft_first_due(_messages):
        [item] = [
            i
            for i in services.platform.follow_ups.for_user("administrator", None)
            if i.kind.value == "client_outreach"
        ]
        return call("draft_follow_up", follow_up_id=item.id)

    runner, _, _ = runner_for(
        services,
        [
            call("run_follow_up_sweep"),
            call("list_due_follow_ups"),
            draft_first_due,
            say("Drafted 1 client follow-up for approval."),
        ],
        clock,
    )
    run = runner.start("follow_up", None, ADMIN.user_id)
    assert run.status is RunStatus.DONE, run.error
    drafts = pending_approvals(services, GateType.FIRST_CLIENT_EMAIL)
    assert len(drafts) == 1 and drafts[0].content["subject"].startswith("Re:")


# ----- API, scheduler, wiring ----------


def test_agents_api_runs_in_the_background_and_takes_decisions():
    client, services, _, clock, job_id = platform_with_job()
    assert ok(client.get("/agents", headers=h(REC)))["enabled"] is False
    assert (
        client.post(
            "/agents/recruiting/runs", json={"subject_id": job_id}, headers=h(REC)
        ).status_code
        == 503
    )
    runner_for(
        services,
        [
            call("task", "t1", description="Widen", subagent_type="sourcer"),
            call("widen_search", location="state"),
            say("Widened to Michigan."),
            say("Widened."),
        ],
        clock,
    )
    assert (
        client.post(
            "/agents/recruiting/runs", json={"subject_id": job_id}, headers=h(BD)
        ).status_code
        == 403
    )
    assert client.post("/agents/intake/runs", json={}, headers=h(ADMIN)).status_code == 400
    assert (
        client.post(
            "/agents/recruiting/runs", json={"subject_id": "nope"}, headers=h(REC)
        ).status_code
        == 404
    )
    started = client.post(
        "/agents/recruiting/runs",
        headers=h(REC),
        json={"subject_id": job_id, "instruction": "Focus on Michigan"},
    )
    assert started.status_code == 202
    run = ok(client.get(f"/agents/runs/{started.json()['id']}", headers=h(REC)))
    assert run["status"] == "waiting_for_human"
    assert run["pending"][0]["action_requests"][0]["name"] == "widen_search"
    bad = client.post(
        f"/agents/runs/{run['id']}/decisions",
        headers=h(REC),
        json={"decisions": [{"type": "approve"}, {"type": "approve"}]},
    )
    assert bad.status_code == 422
    ok(
        client.post(
            f"/agents/runs/{run['id']}/decisions",
            headers=h(REC),
            json={"decisions": [{"type": "approve"}]},
        )
    )
    run = ok(client.get(f"/agents/runs/{run['id']}", headers=h(REC)))
    assert run["status"] == "done" and run["summary"] == "Widened."
    assert [
        r["id"]
        for r in ok(client.get("/agents/runs", params={"agent": "recruiting"}, headers=h(REC)))
    ] == [run["id"]]
    assert (
        client.get(
            "/agents/runs", params={"agent": "business_development"}, headers=h(REC)
        ).status_code
        == 403
    )
    assert "Focus on Michigan" in services.agents._model.seen[0][-1].content


def test_scheduler_starts_agent_runs_and_daily_limit():
    _, services, _, clock, job_id = platform_with_job()
    runner, _, _ = runner_for(
        services, [say("Nothing to do yet."), say("Swept."), say("No prospects.")], clock
    )
    jobs = {j.name: j for j in default_jobs(services)}
    assert {"recruiting_agents", "follow_up_agent", "business_development_agent"} <= set(jobs)
    assert jobs["recruiting_agents"].run() == {"runs": 1, "failed": 0, "waiting": 0, "actions": 0}
    assert runner.jobs_to_work() == []  # ran recently: not again for 6 hours
    jobs["follow_up_agent"].run()
    jobs["business_development_agent"].run()
    assert {r.agent for r in runner.runs.list()} == {
        "recruiting",
        "follow_up",
        "business_development",
    }
    runner._daily_limit = 3
    from recruiting.agentic.runs import AgentError

    with pytest.raises(AgentError, match="daily limit"):
        runner.create("follow_up", None, "scheduler")


def test_every_agent_has_the_rules_and_no_send_or_approve_tool():
    for spec in SPECS.values():
        assert "You never send anything" in spec.system_prompt
        for _name, _desc, prompt, _tools in spec.subagents:
            assert "You never send anything" in prompt
    assert SPECS["recruiting"].interrupt == ("widen_search",)


def test_chat_model_settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from recruiting.agentic.model import FALLBACK_BETA, build_chat_model

    model = build_chat_model("claude-opus-5-5", "high")
    assert model.model == "claude-opus-5-5" and model.output_config == {"effort": "high"}
    assert model.betas == [FALLBACK_BETA] and model.model_kwargs == {"fallbacks": "default"}
    assert model.thinking is None  # Opus 5.5 thinks adaptively; it cannot be turned off


def test_agents_need_a_model():
    from recruiting.api.services import build_services
    from recruiting.config import Settings

    with pytest.raises(RuntimeError, match="RAP_LLM_PROVIDER=claude"):
        build_services(Settings(agents_enabled=True, llm_provider="none", _env_file=None))
