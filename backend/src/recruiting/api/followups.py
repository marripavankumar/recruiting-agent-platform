"""Follow-up API (Agent 12, plan section 11) and the Administrator's cadence settings (Q13).

`POST /follow-ups/run` is idempotent: a scheduler (n8n or cron) calls it every hour, and staff
can call it to refresh their list. Message follow-ups that fall due are drafted into the
approval queue; nothing is sent without approval.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from recruiting.agents.follow_up import FollowUpAgent, FollowUpError
from recruiting.api.deps import ServicesDep, UserDep, require_platform, require_role
from recruiting.domain import Role
from recruiting.records.followups import InvalidCadence
from recruiting.records.models import FollowUpKind

router = APIRouter(tags=["follow-ups"])

STAFF = (
    Role.ADMINISTRATOR, Role.BD_MANAGER, Role.RECRUITER, Role.ACCOUNT_MANAGER,
    Role.SALES_MANAGER,
)  # fmt: skip


class CompleteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class SnoozeIn(BaseModel):
    until: datetime


class CadenceIn(BaseModel):
    days: list[int] = Field(min_length=1, max_length=5)


def _agent(services) -> FollowUpAgent:
    return require_platform(services).follow_ups


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Follow-up not found") from exc
    except FollowUpError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/follow-ups")
def list_follow_ups(
    user: UserDep,
    services: ServicesDep,
    scope: Literal["mine", "all"] = "mine",
    kind: FollowUpKind | None = None,
) -> list[dict]:
    """The signed-in user's open follow-ups, soonest first (Administrator: scope=all)."""
    require_role(user, STAFF)
    agent = _agent(services)
    if scope == "all" and user.role is not Role.ADMINISTRATOR:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the Administrator sees all")
    items = agent.for_user(user.role.value, user.user_id)
    return [asdict(i) for i in items if kind is None or i.kind is kind]


@router.get("/follow-ups/summary")
def summary(user: UserDep, services: ServicesDep) -> dict:
    require_role(user, STAFF)
    return _agent(services).summary()


@router.post("/follow-ups/run")
def run(user: UserDep, services: ServicesDep, draft: bool = Query(True)) -> dict:
    require_role(user, STAFF)
    return asdict(_agent(services).run(auto_draft=draft))


@router.post("/follow-ups/{follow_up_id}/complete")
def complete(follow_up_id: str, body: CompleteIn, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, STAFF)
    return asdict(_call(_agent(services).complete, follow_up_id, user.user_id, body.note))


@router.post("/follow-ups/{follow_up_id}/snooze")
def snooze(follow_up_id: str, body: SnoozeIn, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, STAFF)
    return asdict(_call(_agent(services).snooze, follow_up_id, body.until, user.user_id))


@router.post("/follow-ups/{follow_up_id}/draft")
def draft_now(follow_up_id: str, user: UserDep, services: ServicesDep) -> dict:
    """Draft the next follow-up message now instead of waiting for its due date."""
    require_role(user, STAFF)
    return _call(_agent(services).draft_now, follow_up_id, user.user_id)


@router.get("/admin/follow-up-cadence")
def get_cadence(user: UserDep, services: ServicesDep) -> dict[str, list[int]]:
    require_role(user, (Role.ADMINISTRATOR,))
    return {k.value: v for k, v in require_platform(services).follow_up_cadence.get().items()}


@router.put("/admin/follow-up-cadence/{kind}")
def set_cadence(
    kind: FollowUpKind, body: CadenceIn, user: UserDep, services: ServicesDep
) -> dict[str, list[int]]:
    """Q13: days after the item's event (negative: before, e.g. before a start date)."""
    require_role(user, (Role.ADMINISTRATOR,))
    store = require_platform(services).follow_up_cadence
    try:
        store.save(kind, body.days, user.user_id)
    except InvalidCadence as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {kind.value: store.get()[kind]}
