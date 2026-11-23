"""Job Database API (BRD section 8; Agents 4-5 review step, plan section 7).

Staff see every job; a Client user sees only their own company's jobs (Q8).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from recruiting.api.deps import ServicesDep, UserDep, require_platform, require_role
from recruiting.approvals import Reviewer
from recruiting.domain import Role
from recruiting.identity import CurrentUser
from recruiting.records.models import Job, JobStatus, Priority

router = APIRouter(prefix="/jobs", tags=["jobs"])

EDITORS = (Role.ADMINISTRATOR, Role.ACCOUNT_MANAGER, Role.RECRUITER)
STAFF = (
    Role.ADMINISTRATOR,
    Role.BD_MANAGER,
    Role.RECRUITER,
    Role.ACCOUNT_MANAGER,
    Role.SALES_MANAGER,
)


class JobUpdate(BaseModel):
    """Account Manager review of the extracted fields. Fields sent are taken as confirmed."""

    title: str | None = None
    responsibilities: str | None = None
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    location: str | None = None
    experience: str | None = None
    visa_requirements: str | None = None
    education: str | None = None
    industry: str | None = None
    travel: str | None = None
    work_mode: Literal["onsite", "remote", "hybrid"] | None = None
    salary: str | None = None
    contract_type: str | None = None
    priority: Priority | None = None
    status: JobStatus | None = None
    recruiter_id: str | None = None
    confirm_fields: list[str] = []  # also mark these as checked without changing them


def _visible(job: Job | None, user: CurrentUser) -> bool:
    if job is None:
        return False
    return user.role is not Role.CLIENT or job.client_company_id == user.client_company_id


def _job_out(job: Job, user: CurrentUser) -> dict:
    out = asdict(job)
    if user.role is Role.CLIENT:
        # Internal working data stays internal.
        for key in ("recruiter_id", "review_fields", "analysis", "source_email_ref"):
            out.pop(key, None)
    return out


@router.get("")
def list_jobs(
    user: UserDep,
    services: ServicesDep,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
) -> list[dict]:
    platform = require_platform(services)
    company = user.client_company_id if user.role is Role.CLIENT else None
    jobs = platform.jobs.list(client_company_id=company, status=status_filter)
    return [_job_out(j, user) for j in sorted(jobs, key=lambda j: j.job_code, reverse=True)]


@router.get("/search")
def search_jobs(
    user: UserDep,
    services: ServicesDep,
    q: str = Query(min_length=2),
    limit: int = Query(10, ge=1, le=50),
) -> list[dict]:
    """Semantic search over analysed JDs (plan section 5.4)."""
    require_role(user, STAFF)
    platform = require_platform(services)
    return [
        {"job_id": hit.id, "score": round(hit.score, 4), **hit.payload}
        for hit in platform.search.search("job", q, limit)
    ]


@router.get("/{job_id}")
def get_job(job_id: str, user: UserDep, services: ServicesDep) -> dict:
    platform = require_platform(services)
    job = platform.jobs.get(job_id)
    if not _visible(job, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return _job_out(job, user)


@router.patch("/{job_id}")
def update_job(job_id: str, body: JobUpdate, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, EDITORS)
    platform = require_platform(services)
    job = platform.jobs.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    changes = body.model_dump(exclude_unset=True, exclude={"confirm_fields"})
    was_open = job.status is JobStatus.OPEN
    for name, value in changes.items():
        setattr(job, name, value)
    confirmed = set(changes) | set(body.confirm_fields)
    job.review_fields = [f for f in job.review_fields if f not in confirmed]
    if changes.keys() - {"status", "priority", "recruiter_id"}:
        job = platform.jd_analysis.analyse(job)  # refresh the search profile
        job.review_fields = [f for f in job.review_fields if f not in confirmed]
    platform.jobs.save(job)
    out = _job_out(job, user)
    if was_open and job.status in (JobStatus.FILLED, JobStatus.CLOSED):
        # Scenario S22: stop all open outreach, submissions and follow-ups for this job.
        out["stopped"] = platform.follow_ups.close_job(
            job.id, Reviewer(user.user_id, user.role), f"Job {job.job_code} is {job.status}"
        )
    return out
