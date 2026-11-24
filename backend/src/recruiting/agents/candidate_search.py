"""Agent 6 - Candidate Search (BRD Agent 6, plan section 8).

Search order (Q5, Q6): the platform's own Candidate Database first, then licensed resume
databases and job boards through source adapters, then LinkedIn through compliant tools only.
Internal search combines a skill filter with semantic search against Agent 5's profile.
Candidates from external sources are written back to the Candidate Database (with source and
consent status "none") before ranking, so every ranked candidate is a database record.

Scenario S12: when a ranking holds fewer than 10 qualified candidates (BRD section 12 target
10-20 per JD), the recruiter is alerted (Agent 12 task) and offered ways to widen the search:
title variants, a wider location for the external sources, nice-to-have skills in the skill
filter. A widened search runs only with the options the recruiter chooses, and is recorded on
the ranking.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from recruiting.agents.hiring_signals import target_role
from recruiting.agents.ranking import CandidateRankingAgent
from recruiting.agents.resume_intake import ResumeIntake
from recruiting.ai.search import SemanticSearch
from recruiting.ai.skills import canonical
from recruiting.connectors.retry import call_with_retry
from recruiting.records.memory import CandidateRepository
from recruiting.records.models import Candidate, Job, Ranking, Verdict

MIN_QUALIFIED = 10  # BRD section 12: 10-20 qualified candidates per JD

# Neighbouring titles recruiters search when a role is thin (BRD section 4 positions).
RELATED_TITLES: dict[str, list[str]] = {
    "Quality Engineer": ["Supplier Quality Engineer", "Quality Assurance Engineer"],
    "Manufacturing Engineer": ["Process Engineer", "Industrial Engineer", "Production Engineer"],
    "Process Engineer": ["Manufacturing Engineer", "Industrial Engineer"],
    "Production Engineer": ["Manufacturing Engineer", "Process Engineer"],
    "Industrial Engineer": ["Manufacturing Engineer", "Process Engineer"],
    "Controls Engineer": ["PLC Engineer", "Automation Engineer"],
    "PLC Engineer": ["Controls Engineer", "Automation Engineer"],
    "Automation Engineer": ["Controls Engineer", "Robotics Engineer"],
    "Robotics Engineer": ["Automation Engineer", "Controls Engineer"],
    "Design Engineer": ["Product Engineer", "Mechanical Engineer"],
    "Product Engineer": ["Design Engineer", "Mechanical Engineer"],
    "Mechanical Engineer": ["Design Engineer", "Product Engineer"],
    "CAE Engineer": ["FEA Engineer", "Simulation Engineer"],
    "FEA Engineer": ["CAE Engineer", "Stress Engineer"],
    "Validation Engineer": ["Test Engineer", "Reliability Engineer"],
    "Test Engineer": ["Validation Engineer"],
    "Reliability Engineer": ["Validation Engineer", "Quality Engineer"],
    "Electrical Engineer": ["Electronics Engineer", "Controls Engineer"],
    "Electronics Engineer": ["Electrical Engineer"],
}
_SENIORITY = re.compile(r"\b(senior|sr|junior|jr|lead|principal|staff|ii|iii|iv|[1-4])\b\.?", re.I)
_STATE = re.compile(r",\s*([A-Z]{2})\b")
LOCATION_SCOPES = ("state", "anywhere")


def title_variants(title: str) -> list[str]:
    """Other titles worth searching for this job, best first (the job's own title excluded)."""
    base = " ".join(_SENIORITY.sub(" ", title).split())
    role = target_role(title)
    seen, out = {title.strip().lower()}, []
    for t in [base, role or "", *RELATED_TITLES.get(role or "", [])]:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def widened_location(job: Job, scope: str | None) -> str | None:
    """The location the external sources are asked for."""
    if scope == "anywhere":
        return None
    if scope == "state" and job.location:
        state = _STATE.search(job.location)
        return state.group(1) if state else job.location
    return job.location


def qualified_count(ranking: Ranking) -> int:
    """Ranked candidates the recruiter has not marked as not qualified."""
    return sum(1 for c in ranking.top if c.verdict is not Verdict.NOT_QUALIFIED)


def shortfall(job: Job, ranking: Ranking) -> dict[str, Any] | None:
    """S12: None when the ranking has enough qualified candidates, else the widening options."""
    count = qualified_count(ranking)
    if count >= MIN_QUALIFIED:
        return None
    locations: list[str] = []
    if job.location:
        locations = list(LOCATION_SCOPES) if _STATE.search(job.location) else ["anywhere"]
    nice = (job.analysis or {}).get("nice_to_have_skills") or job.preferred_skills
    return {
        "qualified": count,
        "target": MIN_QUALIFIED,
        "widened": ranking.widened is not None,
        "suggestions": {
            "title_variants": title_variants(job.title),
            "location": locations,
            "include_nice_to_have": bool(nice),
        },
    }


@dataclass(frozen=True)
class Widening:
    """Search options the recruiter chose (S12)."""

    title_variants: tuple[str, ...] = ()
    location: str | None = None  # None (as the JD says), "state" or "anywhere"
    include_nice_to_have: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "title_variants": list(self.title_variants),
            "location": self.location,
            "include_nice_to_have": self.include_nice_to_have,
        }


@dataclass(frozen=True)
class ExternalResume:
    """A resume returned by a licensed source (Dice, CareerBuilder, Monster, Indeed, ...)."""

    source: str
    resume_text: str


class CandidateSource(Protocol):
    name: str

    def search(
        self, query: str, skills: Sequence[str], location: str | None, limit: int
    ) -> Iterable[ExternalResume]: ...


@dataclass
class SearchReport:
    internal_semantic: int = 0
    internal_skill: int = 0
    external_found: dict[str, int] = field(default_factory=dict)
    external_new: int = 0
    external_errors: dict[str, str] = field(default_factory=dict)  # S24: shown to the recruiter
    pool: int = 0


def search_query(job: Job, title: str | None = None) -> str:
    analysis = job.analysis or {}
    return "\n".join(
        part
        for part in [
            title or job.title,
            analysis.get("search_summary", ""),
            ", ".join(analysis.get("must_have_skills", []) or job.required_skills),
            ", ".join(analysis.get("nice_to_have_skills", []) or job.preferred_skills),
            job.industry or "",
        ]
        if part
    )


class CandidateSearchAgent:
    def __init__(
        self,
        candidates: CandidateRepository,
        search: SemanticSearch,
        intake: ResumeIntake,
        ranking: CandidateRankingAgent,
        sources: Sequence[CandidateSource] = (),
        pool_size: int = 200,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._candidates = candidates
        self._search = search
        self._intake = intake
        self._ranking = ranking
        self._sources = sources
        self._pool_size = pool_size

    def find(self, job: Job, widen: Widening | None = None) -> tuple[list[Candidate], SearchReport]:
        report = SearchReport()
        widen = widen or Widening()
        query = search_query(job)
        analysis = job.analysis or {}
        must = [
            canonical(s).lower() for s in (analysis.get("must_have_skills") or job.required_skills)
        ]
        filter_skills = set(must)
        if widen.include_nice_to_have:
            nice = analysis.get("nice_to_have_skills") or job.preferred_skills
            filter_skills |= {canonical(s).lower() for s in nice}

        pool: dict[str, Candidate] = {}
        queries = [query, *(search_query(job, title=t) for t in widen.title_variants)]
        for q in queries:
            for hit in self._search.search("candidate", q, limit=self._pool_size):
                candidate = None if hit.id in pool else self._candidates.get(hit.id)
                if candidate:
                    pool[candidate.id] = candidate
                    report.internal_semantic += 1
        # Skill filter catches strong matches the lexical embedder ranks low.
        if filter_skills:
            for candidate in self._candidates.list(limit=10_000):
                if candidate.id not in pool and any(
                    s.lower() in filter_skills for s in candidate.skills
                ):
                    pool[candidate.id] = candidate
                    report.internal_skill += 1

        location = widened_location(job, widen.location)
        for source in self._sources:
            found: list[ExternalResume] = []
            try:
                for q in queries:
                    found += call_with_retry(
                        lambda q=q, source=source: list(source.search(q, must, location, 50)),
                        sleep=self._sleep,
                    )
            except Exception as exc:  # S24: keep the other sources' results; tell the recruiter
                report.external_errors[source.name] = f"{type(exc).__name__}: {exc}"[:300]
            report.external_found[source.name] = len(found)
            for resume in found:
                outcome = self._intake.add(resume.resume_text, source=resume.source)
                report.external_new += int(outcome.created)
                pool[outcome.candidate.id] = outcome.candidate

        report.pool = len(pool)
        return list(pool.values()), report

    def run(
        self, job: Job, limit: int = 20, widen: Widening | None = None
    ) -> tuple[Ranking, SearchReport]:
        candidates, report = self.find(job, widen)
        return self._ranking.rank(job, candidates, limit=limit), report
