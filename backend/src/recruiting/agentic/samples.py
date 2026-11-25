"""Sample AI agent runs for the demo and the test data (never for production).

The deep agents need Claude, so the local demo and the test-data loader run with them off. These
records give the AI agents screen something real to show: one run of each agent, including a
recruiting run waiting for the recruiter's decision (S12, widened search) and one that failed.
They point at the jobs, candidates, companies, approvals and follow-ups the seed created, so
every link on the screen leads somewhere. Each summary says it is sample data.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from recruiting.agentic.runs import AgentRun, RunStatus
from recruiting.domain import GateType
from recruiting.records.memory import new_id

if TYPE_CHECKING:
    from recruiting.api.platform import Platform
    from recruiting.approvals import ApprovalEngine

NOTE = "Sample run from the test data: the AI agents are off here, so this was not run by Claude."


def sample_runs(
    platform: Platform,
    approvals: ApprovalEngine,
    started_by: dict[str, str],  # role value -> user id
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[AgentRun]:
    now = clock()
    jobs = sorted(platform.jobs.list(), key=lambda j: j.job_code)
    runs: list[AgentRun] = []

    def run(agent: str, ago: timedelta, role: str, status: RunStatus, subject: str | None = None,
            actions: list[tuple[str, int, dict[str, Any]]] = (), **extra: Any) -> None:  # fmt: skip
        started = now - ago
        steps = [
            {"tool": tool, "at": (started + timedelta(seconds=secs)).isoformat(), **details}
            for tool, secs, details in actions
        ]
        ended = started + timedelta(seconds=(actions[-1][1] + 20) if actions else 30)
        runs.append(
            AgentRun(
                new_id(),
                agent,
                subject,
                status,
                started_by.get(role, role),
                started,
                ended,
                actions=steps,
                **extra,
            )  # fmt: skip
        )

    if jobs:
        job = jobs[0]
        ranking = platform.rankings.get(job.id)
        top = ranking.top if ranking else []
        ids = [c.candidate_id for c in top]
        steps: list[tuple[str, int, dict[str, Any]]] = [
            ("get_job", 4, {"job_id": job.id}),
            (
                "search_and_rank",
                19,
                {"job_id": job.id, "ranked": len(top), "pool": ranking.searched if ranking else 0},
            ),  # fmt: skip
            (
                "draft_outreach",
                41,
                {
                    "job_id": job.id,
                    "drafted": min(3, len(ids)),
                    "skipped": {"already_contacted": 1},
                },
            ),  # fmt: skip
        ]
        if ids:
            steps.append(("request_candidate_details", 58, {"job_id": job.id,
                                                            "candidate_id": ids[0]}))  # fmt: skip
            steps.append(("match_candidate", 77, {"job_id": job.id, "candidate_id": ids[0],
                                                  "score": top[0].score}))  # fmt: skip
        best = f" {len(top)} candidates ranked; the best scores {top[0].score}." if top else ""
        run(
            "recruiting", timedelta(days=2, hours=3), "recruiter", RunStatus.DONE, job.id, steps,
            summary=(
                f"{job.job_code} {job.title}:{best} Drafted outreach for the top three "
                "(waiting in the approval queue; one was already contacted, so skipped), asked "
                "the strongest candidate for their resume details, and prepared a match report "
                "for submission. Next: approve the drafts, then the submission once consent is "
                f"in.\n\n{NOTE}"
            ),
        )  # fmt: skip

        second = jobs[1] if len(jobs) > 1 else job
        ranking = platform.rankings.get(second.id)
        ranked = len(ranking.top) if ranking else 0
        widen = {
            "title_variants": [f"Senior {second.title}", f"{second.title} II"],
            "location": "state",
            "include_nice_to_have": True,
        }
        run(
            "recruiting", timedelta(hours=3), "recruiter", RunStatus.WAITING, second.id,
            [("get_job", 3, {"job_id": second.id}),
             ("search_and_rank", 17, {"job_id": second.id, "ranked": ranked,
                                      "pool": ranking.searched if ranking else 0})],
            pending=[{
                "interrupt_id": new_id(),
                "action_requests": [{
                    "name": "widen_search",
                    "args": widen,
                    "description": (f"Only {ranked} candidates qualify" if ranked
                                    else "No candidates qualify yet")
                    + " (10 wanted): search related titles across the state, with the "
                    "nice-to-have skills.",
                }],
                "review_configs": [{"action_name": "widen_search",
                                    "allowed_decisions": ["approve", "edit", "reject"]}],
            }],
        )  # fmt: skip

    companies = sorted(platform.companies.list(), key=lambda c: -c.priority_score)
    drafts = [a for a in approvals.pending() if a.gate is GateType.FIRST_CLIENT_EMAIL]
    if companies:
        steps = [
            ("run_company_discovery", 12, {"created": 0, "merged": len(companies)}),
            ("check_hiring_signals", 48, {"checked": len(companies)}),
            ("list_prospects", 52, {"min_priority": 50}),
        ] + [
            ("draft_first_email", 70 + 15 * i, {"company_id": a.subject_id,
                                                "approval_request_id": a.id})
            for i, a in enumerate(drafts[:2])
        ]  # fmt: skip
        names = ", ".join(c.name for c in companies[:3])
        run(
            "business_development", timedelta(days=1, hours=5), "bd_manager", RunStatus.DONE,
            actions=steps,
            summary=(
                f"Checked career pages for {len(companies)} companies. Strongest hiring "
                f"signals: {names}. Drafted {len(drafts[:2])} first emails; they wait for the BD "
                f"Manager in the approval queue.\n\n{NOTE}"
            ),
        )  # fmt: skip
        run(
            "business_development", timedelta(days=4), "bd_manager", RunStatus.FAILED,
            actions=[("run_company_discovery", 10, {"created": 0, "merged": len(companies)})],
            error="APIConnectionError: Connection error. (sample: the model could not be "
            "reached; nothing was drafted, and the run can be started again)",
        )  # fmt: skip

    run(
        "follow_up", timedelta(hours=20), "account_manager", RunStatus.DONE,
        actions=[("run_follow_up_sweep", 6, {"created": 0, "closed": 0}),
                 ("list_due_follow_ups", 9, {})],
        summary=(
            "Follow-ups are up to date. Nothing was due for a message yet; the earliest client "
            f"follow-ups fall due in the next few days.\n\n{NOTE}"
        ),
    )  # fmt: skip

    if jobs:
        run(
            "intake", timedelta(days=3), "administrator", RunStatus.DONE,
            actions=[("lookup_sender", 2, {}),
                     ("file_client_reply", 9, {"category": "jd_received",
                                               "job_ids": [jobs[0].id]})],
            summary=f"Filed a client reply with a job description as {jobs[0].job_code}.\n\n{NOTE}",
        )  # fmt: skip
    return runs
