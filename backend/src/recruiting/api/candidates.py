"""Candidate Database and ranking API (Agents 6-7, plan section 8).

Recruiters, Account Managers and Administrators only: candidate data never reaches a Client
user until a submission is approved (Phase 6).
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from recruiting.agents.ranking import RankingWeights
from recruiting.api.deps import ServicesDep, UserDep, require_platform, require_role
from recruiting.domain import Role
from recruiting.records.models import ConsentStatus, Ranking, Verdict
from recruiting.records.rankings import UnknownRankingEntry

router = APIRouter(tags=["candidates"])

TALENT = (Role.ADMINISTRATOR, Role.RECRUITER, Role.ACCOUNT_MANAGER)


class ResumeIn(BaseModel):
    resume_text: str = Field(min_length=20, max_length=200_000)
    source: str = Field(default="manual", max_length=80)
    consent: ConsentStatus = ConsentStatus.NONE


class ResumesIn(BaseModel):
    resumes: list[ResumeIn] = Field(min_length=1, max_length=200)


class ReviewIn(BaseModel):
    verdict: Verdict
    note: str | None = Field(default=None, max_length=2000)


class MoveIn(BaseModel):
    rank: int = Field(ge=1, le=200)


class WeightsIn(BaseModel):
    skill_match: float = Field(ge=0)
    industry_match: float = Field(ge=0)
    years_experience: float = Field(ge=0)
    project_similarity: float = Field(ge=0)
    recent_experience: float = Field(ge=0)
    location: float = Field(ge=0)
    availability: float = Field(ge=0)
    education: float = Field(ge=0)
    resume_quality: float = Field(ge=0)
    communication: float = Field(ge=0)
    min_must_have_coverage: float = Field(ge=0, le=1)


def _candidate_out(candidate, full: bool = False) -> dict:
    out = asdict(candidate)
    if not full:
        out.pop("resume_text", None)
    return out


def _ranking_out(ranking: Ranking, services) -> dict:
    platform = services.platform
    rows = []
    for entry in ranking.candidates:
        candidate = platform.candidates.get(entry.candidate_id)
        rows.append(
            {
                **asdict(entry),
                "full_name": candidate.full_name if candidate else None,
                "headline": (
                    candidate.work_history[0].get("title")
                    if candidate and candidate.work_history
                    else None
                ),
                "location": candidate.current_location if candidate else None,
                "source": candidate.source if candidate else None,
            }
        )
    return {
        "job_id": ranking.job_id,
        "ranked_at": ranking.ranked_at,
        "searched": ranking.searched,
        "weights": ranking.weights,
        "qualified_in_top_20": ranking.qualified_in_top_20,
        "candidates": rows,
    }


@router.post("/candidates", status_code=status.HTTP_201_CREATED)
def add_candidates(body: ResumesIn, user: UserDep, services: ServicesDep) -> dict:
    """Add resumes to the Candidate Database (parsed, de-duplicated, indexed for search)."""
    require_role(user, TALENT)
    platform = require_platform(services)
    results = [platform.resume_intake.add(r.resume_text, r.source, r.consent) for r in body.resumes]
    return {
        "created": sum(r.created for r in results),
        "updated": sum(not r.created for r in results),
        "candidates": [
            {
                "id": r.candidate.id,
                "full_name": r.candidate.full_name,
                "created": r.created,
                "parsed_by": r.parsed_by,
            }
            for r in results
        ],
    }


@router.get("/candidates")
def list_candidates(
    user: UserDep, services: ServicesDep, limit: int = Query(default=100, ge=1, le=1000)
) -> list[dict]:
    require_role(user, TALENT)
    return [_candidate_out(c) for c in require_platform(services).candidates.list(limit=limit)]


@router.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: str, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, TALENT)
    candidate = require_platform(services).candidates.get(candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    return _candidate_out(candidate, full=True)


@router.post("/jobs/{job_id}/ranking")
def run_ranking(
    job_id: str, user: UserDep, services: ServicesDep, limit: int = Query(default=20, ge=1, le=100)
) -> dict:
    """Agent 6 searches, Agent 7 ranks; the result replaces the job's previous ranking."""
    require_role(user, TALENT)
    platform = require_platform(services)
    job = platform.jobs.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    ranking, report = platform.candidate_search.run(job, limit=limit)
    ranking = platform.rankings.save(ranking)
    return {**_ranking_out(ranking, services), "search": asdict(report)}


@router.get("/jobs/{job_id}/ranking")
def get_ranking(job_id: str, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, TALENT)
    ranking = require_platform(services).rankings.get(job_id)
    if ranking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No ranking yet for this job")
    return _ranking_out(ranking, services)


@router.post("/jobs/{job_id}/ranking/{candidate_id}/review")
def review_candidate(
    job_id: str, candidate_id: str, body: ReviewIn, user: UserDep, services: ServicesDep
) -> dict:
    """Recruiter verdict; stored to measure and later tune the ranking (plan section 8)."""
    require_role(user, TALENT)
    try:
        entry = require_platform(services).rankings.review(
            job_id, candidate_id, body.verdict, body.note, user.user_id
        )
    except UnknownRankingEntry as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Candidate is not in this job's ranking"
        ) from exc
    return asdict(entry)


@router.post("/jobs/{job_id}/ranking/{candidate_id}/move")
def move_candidate(
    job_id: str, candidate_id: str, body: MoveIn, user: UserDep, services: ServicesDep
) -> dict:
    require_role(user, TALENT)
    try:
        ranking = require_platform(services).rankings.move(job_id, candidate_id, body.rank)
    except UnknownRankingEntry as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Candidate is not ranked for this job"
        ) from exc
    return _ranking_out(ranking, services)


@router.get("/admin/ranking-weights")
def get_weights(user: UserDep, services: ServicesDep) -> dict:
    require_role(user, (Role.ADMINISTRATOR,))
    return asdict(require_platform(services).ranking_weights.get())


@router.put("/admin/ranking-weights")
def put_weights(body: WeightsIn, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, (Role.ADMINISTRATOR,))
    try:
        weights = RankingWeights(**body.model_dump()).validate()
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return asdict(require_platform(services).ranking_weights.save(weights, user.user_id))
