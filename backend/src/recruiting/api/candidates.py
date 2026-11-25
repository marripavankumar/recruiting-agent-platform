"""Candidate Database and ranking API (Agents 6-7, plan section 8).

Recruiters, Account Managers and Administrators only: candidate data never reaches a Client
user until a submission is approved (Phase 6).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from recruiting.agents.candidate_search import CandidateFilter, Widening, shortfall
from recruiting.agents.ranking import RankingWeights
from recruiting.agents.resume_intake import ResumeFile
from recruiting.api.deps import ServicesDep, UserDep, require_platform, require_role
from recruiting.documents import MAX_BYTES, DocumentError, extract_text
from recruiting.domain import Role
from recruiting.records.models import ConsentStatus, Ranking, Verdict
from recruiting.records.rankings import UnknownRankingEntry
from recruiting.storage import StorageError, StoredFile

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


class WidenIn(BaseModel):
    """S12: the recruiter's choice of how to widen the search (their approval to run it)."""

    title_variants: list[str] = Field(default_factory=list, max_length=10)
    location: Literal["state", "anywhere"] | None = None
    include_nice_to_have: bool = False


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
    job = platform.jobs.get(ranking.job_id)
    return {
        "job_id": ranking.job_id,
        "ranked_at": ranking.ranked_at,
        "searched": ranking.searched,
        "weights": ranking.weights,
        "qualified_in_top_20": ranking.qualified_in_top_20,
        "widened": ranking.widened,
        "shortfall": shortfall(job, ranking) if job else None,  # S12
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


@router.post("/candidates/upload")
def upload_resumes(
    user: UserDep,
    services: ServicesDep,
    files: Annotated[list[UploadFile], File()],
    source: Annotated[str, Form(max_length=80)] = "manual",
    consent: Annotated[ConsentStatus, Form()] = ConsentStatus.NONE,
) -> dict:
    """Resume files (PDF, Word .docx, text) into the Candidate Database; errors per file."""
    require_role(user, TALENT)
    if len(files) > 20:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Upload at most 20 files")
    platform = require_platform(services)
    added, errors = [], []
    for upload in files:
        name = upload.filename or "resume"
        try:
            data = upload.file.read(MAX_BYTES + 1)
            doc = extract_text(name, data)
        except DocumentError as exc:
            errors.append({"filename": name, "error": str(exc)})
            continue
        result = platform.resume_intake.add(
            doc.text, source, consent, file=ResumeFile(name, data, doc.kind)
        )
        added.append({"filename": name, "kind": doc.kind, "id": result.candidate.id,
                      "full_name": result.candidate.full_name, "created": result.created,
                      "parsed_by": result.parsed_by})  # fmt: skip
    return {
        "created": sum(a["created"] for a in added),
        "updated": sum(not a["created"] for a in added),
        "candidates": added,
        "errors": errors,
    }


SCAN_LIMIT = 20_000  # filters look at up to this many candidates (pilot scale)


@router.get("/candidates")
def list_candidates(
    user: UserDep,
    services: ServicesDep,
    limit: int = Query(default=100, ge=1, le=1000),
    q: str | None = Query(default=None, max_length=200, description="Name, skill, location, title"),
    title: str | None = Query(default=None, max_length=200),
    skills: str | None = Query(default=None, max_length=500, description="Comma-separated; all"),
    industry: str | None = Query(default=None, max_length=100),
    min_years: float | None = Query(default=None, ge=0, le=60),
    education: str | None = Query(default=None, max_length=100),
    location: str | None = Query(default=None, max_length=100),
    visa: str | None = Query(default=None, max_length=100),
    availability: str | None = Query(default=None, max_length=100),
    max_hourly_rate: float | None = Query(default=None, gt=0, le=10_000),
    has_linkedin: bool | None = None,
    has_resume: bool | None = None,
) -> list[dict]:
    """The Candidate Database, with the BRD Agent 6 candidate filters."""
    require_role(user, TALENT)
    wanted = CandidateFilter(
        text=(q or "").strip() or None,
        title=title,
        skills=tuple(s.strip() for s in (skills or "").split(",") if s.strip()),
        industry=industry,
        min_years=min_years,
        education=education,
        location=location,
        visa=visa,
        availability=availability,
        max_hourly_rate=max_hourly_rate,
        has_linkedin=has_linkedin,
        has_resume=has_resume,
    )
    repo = require_platform(services).candidates
    if wanted == CandidateFilter():
        return [_candidate_out(c) for c in repo.list(limit=limit)]
    found = [c for c in repo.list(limit=SCAN_LIMIT) if wanted.matches(c)]
    return [_candidate_out(c) for c in found[:limit]]


@router.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: str, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, TALENT)
    candidate = require_platform(services).candidates.get(candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    return _candidate_out(candidate, full=True)


@router.get("/candidates/{candidate_id}/resume-file")
def download_resume_file(candidate_id: str, user: UserDep, services: ServicesDep) -> Response:
    """The original resume file as uploaded (PDF, Word or text), for the same roles that see the
    candidate. 404 when only resume text is known."""
    require_role(user, TALENT)
    platform = require_platform(services)
    candidate = platform.candidates.get(candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    stored = StoredFile.from_dict(candidate.resume_file)
    if stored is None or platform.documents is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No original resume file is stored")
    try:
        data = platform.documents.get(stored.key)
    except StorageError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "The resume file is no longer stored"
        ) from exc
    return Response(
        data,
        media_type=stored.content_type,
        headers={
            # ASCII fallback plus the exact name (RFC 6266 / 5987) for names like "José.pdf".
            "Content-Disposition": 'attachment; filename="'
            + (stored.filename.encode("ascii", "replace").decode().replace("?", "_"))
            + f"\"; filename*=UTF-8''{quote(stored.filename)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


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


@router.post("/jobs/{job_id}/ranking/widen")
def widen_ranking(
    job_id: str,
    body: WidenIn,
    user: UserDep,
    services: ServicesDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """S12: re-run Agents 6-7 with the widening the recruiter chose; recorded on the ranking."""
    require_role(user, TALENT)
    platform = require_platform(services)
    job = platform.jobs.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    variants = tuple(dict.fromkeys(t.strip()[:120] for t in body.title_variants if t.strip()))
    widen = Widening(variants, body.location, body.include_nice_to_have)
    if widen == Widening():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose at least one way to widen the search"
        )
    ranking, report = platform.candidate_search.run(job, limit=limit, widen=widen)
    ranking.widened = {
        **widen.as_dict(),
        "by": user.user_id,
        "at": datetime.now(UTC).isoformat(),
        "before": (previous := platform.rankings.get(job_id)) and len(previous.top),
    }
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
