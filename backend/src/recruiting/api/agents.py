"""Agent runs API: start a deep agent, follow it, and decide what it is waiting for.

A run starts in the background and returns at once (202); poll GET /agents/runs/{id}. A run
that waits for a person (for example a widened candidate search, S12) lists its pending actions;
POST /agents/runs/{id}/decisions approves, edits or rejects them and the run continues.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel, Field

from recruiting.agentic.runs import AgentError, AgentRunner, RunStatus, run_out
from recruiting.api.deps import ServicesDep, UserDep, require_platform
from recruiting.records.models import JobStatus

router = APIRouter(prefix="/agents", tags=["agents"])


class StartIn(BaseModel):
    subject_id: str | None = Field(default=None, max_length=100)  # the job id for recruiting
    instruction: str | None = Field(default=None, max_length=2000)


class DecisionIn(BaseModel):
    type: Literal["approve", "edit", "reject"]
    message: str | None = Field(default=None, max_length=2000)  # reason for a rejection
    args: dict[str, Any] | None = None  # the edited tool arguments


class DecisionsIn(BaseModel):
    decisions: list[DecisionIn] = Field(min_length=1, max_length=20)


def _runner(services) -> AgentRunner:
    if services.agents is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Agents are off: set RAP_AGENTS_ENABLED=true with RAP_LLM_PROVIDER=claude",
        )
    return services.agents


def _allowed(runner: AgentRunner, agent: str, user) -> None:
    try:
        spec = runner.spec(agent)
    except AgentError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if user.role.value not in spec.roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your role cannot run the {agent} agent")


@router.get("")
def list_agents(user: UserDep, services: ServicesDep) -> dict:
    from recruiting.agentic.agents import SPECS

    return {
        "enabled": services.agents is not None,
        "agents": [
            {
                "name": s.name,
                "goal": s.goal,
                "roles": list(s.roles),
                "subagents": [x[0] for x in s.subagents],
                "needs_approval": list(s.interrupt),
            }
            for s in SPECS.values()
        ],
    }


@router.post("/{agent}/runs", status_code=status.HTTP_202_ACCEPTED)
def start_run(
    agent: str, body: StartIn, user: UserDep, services: ServicesDep, background: BackgroundTasks
) -> dict:
    runner = _runner(services)
    _allowed(runner, agent, user)
    if agent == "intake":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Intake runs start from the mailbox")
    if agent == "recruiting":
        job = require_platform(services).jobs.get(body.subject_id or "")
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Give the id of a job (subject_id)")
        if job.status is not JobStatus.OPEN:
            raise HTTPException(status.HTTP_409_CONFLICT, f"The job is {job.status.value}")
    try:
        run = runner.create(agent, body.subject_id if agent == "recruiting" else None, user.user_id)
    except AgentError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    background.add_task(
        runner.execute, run.id, instruction=body.instruction, recruiter=user.display_name
    )
    return run_out(run)


@router.get("/runs")
def list_runs(
    user: UserDep,
    services: ServicesDep,
    agent: str | None = None,
    subject_id: str | None = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[dict]:
    runner = _runner(services)
    from recruiting.agentic.agents import SPECS

    visible = [n for n, s in SPECS.items() if user.role.value in s.roles]
    if agent is not None and agent not in visible:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot see these runs")
    runs = runner.runs.list(agent=agent, subject_id=subject_id, status=run_status, limit=limit)
    return [run_out(r) for r in runs if r.agent in visible]


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: UserDep, services: ServicesDep) -> dict:
    runner = _runner(services)
    run = runner.runs.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent run")
    _allowed(runner, run.agent, user)
    return run_out(run)


@router.post("/runs/{run_id}/decisions")
def decide(
    run_id: str,
    body: DecisionsIn,
    user: UserDep,
    services: ServicesDep,
    background: BackgroundTasks,
) -> dict:
    runner = _runner(services)
    run = runner.runs.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent run")
    _allowed(runner, run.agent, user)
    if run.status is not RunStatus.WAITING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The run is {run.status.value}")
    decisions = []
    for d in body.decisions:
        if d.type == "edit":
            if not d.args:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An edit needs args")
            name = run.pending[0]["action_requests"][0]["name"]
            decisions.append({"type": "edit", "edited_action": {"name": name, "args": d.args}})
        elif d.type == "reject":
            decisions.append(
                {"type": "reject", "message": d.message or "Rejected by the recruiter"}
            )
        else:
            decisions.append({"type": "approve"})
    expected = sum(len(p["action_requests"]) for p in run.pending)
    if len(decisions) != expected:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Give {expected} decision(s), one per pending action",
        )
    background.add_task(runner.resume, run_id, decisions, user.user_id, user.display_name)
    return {**run_out(run), "status": "running"}
