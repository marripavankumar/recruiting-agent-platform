"""Client-acquisition API: Company Database and Agents 1-3 (plan sections 5.2 and 6)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from recruiting.agents.discovery import CompanyDiscoveryAgent, CsvCompanySource, DiscoveryCriteria
from recruiting.api.deps import ServicesDep, UserDep, require_platform, require_role
from recruiting.domain import Role

router = APIRouter(prefix="/companies", tags=["companies"])

VIEWERS = (Role.ADMINISTRATOR, Role.BD_MANAGER, Role.SALES_MANAGER, Role.ACCOUNT_MANAGER)
OPERATORS = (Role.ADMINISTRATOR, Role.BD_MANAGER)


class ImportIn(BaseModel):
    source_name: str = Field(min_length=1, max_length=80, description="e.g. apollo-export-2026-11")
    csv: str = Field(min_length=1, max_length=5_000_000)
    min_revenue_usd: float = 100_000_000
    countries: list[str] = ["US", "CA"]


class CompanyIdsIn(BaseModel):
    company_ids: list[str] | None = None  # None: every eligible company


def _company_out(company: Any, contacts: list | None = None, postings: list | None = None) -> dict:
    out = asdict(company)
    if contacts is not None:
        out["contacts"] = [asdict(c) for c in contacts]
    if postings is not None:
        out["postings"] = [asdict(p) for p in postings]
    return out


@router.get("")
def list_companies(
    user: UserDep,
    services: ServicesDep,
    industry: str | None = None,
    min_priority: int | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    require_role(user, VIEWERS)
    platform = require_platform(services)
    return [
        _company_out(c)
        for c in platform.companies.list(industry=industry, min_priority=min_priority, limit=limit)
    ]


@router.get("/{company_id}")
def get_company(company_id: str, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, VIEWERS)
    platform = require_platform(services)
    company = platform.companies.get(company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return _company_out(
        company, platform.companies.contacts(company_id), platform.companies.postings(company_id)
    )


@router.post("/import")
def import_companies(body: ImportIn, user: UserDep, services: ServicesDep) -> dict:
    """Agent 1 over an uploaded list (industry database export, purchased list)."""
    require_role(user, OPERATORS)
    platform = require_platform(services)
    criteria = DiscoveryCriteria(
        min_revenue_usd=body.min_revenue_usd,
        countries=tuple(c.upper() for c in body.countries),
    )
    agent = CompanyDiscoveryAgent(
        platform.companies, [CsvCompanySource(body.source_name, body.csv)]
    )
    return asdict(agent.run(criteria))


@router.post("/hiring-signals")
def check_hiring_signals(body: CompanyIdsIn, user: UserDep, services: ServicesDep) -> dict:
    """Agent 2: read career pages, update postings and priority scores."""
    require_role(user, OPERATORS)
    platform = require_platform(services)
    companies = None
    if body.company_ids is not None:
        companies = [c for cid in body.company_ids if (c := platform.companies.get(cid))]
    return asdict(platform.signals.run(companies))


@router.post("/hiring-signals/run-due")
def check_due_hiring_signals(user: UserDep, services: ServicesDep) -> dict:
    """S4: scheduled sweep (n8n or cron); re-reads only the career pages that are due."""
    require_role(user, OPERATORS)
    return asdict(require_platform(services).signals.run_due())


@router.post("/outreach-drafts")
def draft_outreach(body: CompanyIdsIn, user: UserDep, services: ServicesDep) -> dict:
    """Agent 3: draft first emails; each one waits in the BD Manager's approval queue."""
    require_role(user, OPERATORS)
    platform = require_platform(services)
    if platform.client_outreach is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Outreach workflows are not running"
        )
    companies = None
    if body.company_ids is not None:
        companies = [c for cid in body.company_ids if (c := platform.companies.get(cid))]
    return asdict(platform.client_outreach.run(companies))
