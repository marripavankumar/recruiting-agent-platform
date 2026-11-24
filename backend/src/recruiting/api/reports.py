"""KPI dashboard, operations view, access log and candidate erasure (plan section 12)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from recruiting.api.deps import ServicesDep, UserDep, require_platform, require_role
from recruiting.compliance.erasure import ErasureError
from recruiting.domain import Role

router = APIRouter(tags=["reports"])

STAFF = (
    Role.ADMINISTRATOR, Role.BD_MANAGER, Role.RECRUITER, Role.ACCOUNT_MANAGER,
    Role.SALES_MANAGER,
)  # fmt: skip


class ErasureIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.get("/kpis")
def kpis(user: UserDep, services: ServicesDep, days: int = Query(default=30, ge=7, le=366)) -> dict:
    """BRD section 12 KPIs for the last `days` days, with targets and the basis of each."""
    require_role(user, STAFF)
    return require_platform(services).reporting.kpis(days)


@router.get("/admin/operations")
def operations(request: Request, user: UserDep, services: ServicesDep) -> dict:
    """Monitoring: email failures, waiting approvals, LLM cost, overdue follow-ups, alerts."""
    require_role(user, (Role.ADMINISTRATOR,))
    platform = require_platform(services)
    entries = platform.gateway.ledger.entries() if platform.gateway else []
    denied = len(request.app.state.access_log.recent(500))
    return platform.reporting.operations(platform.follow_ups.summary(), entries, denied)


@router.get("/admin/access-denied")
def access_denied(
    request: Request, user: UserDep, limit: int = Query(default=100, ge=1, le=500)
) -> list[dict]:
    """Scenario S26: refused requests, newest first."""
    require_role(user, (Role.ADMINISTRATOR,))
    return request.app.state.access_log.recent(limit)


@router.post("/candidates/{candidate_id}/erase")
def erase_candidate(
    candidate_id: str, body: ErasureIn, user: UserDep, services: ServicesDep
) -> dict:
    """Scenario S23: the candidate asked for their data to be deleted."""
    require_role(user, (Role.ADMINISTRATOR,))
    try:
        return require_platform(services).erasure.erase(candidate_id, user.user_id, body.reason)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found") from exc
    except ErasureError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
