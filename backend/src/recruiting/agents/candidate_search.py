"""Agent 6 - Candidate Search (BRD Agent 6, plan section 8).

Search order (Q5, Q6): the platform's own Candidate Database first, then licensed resume
databases and job boards through source adapters, then LinkedIn through compliant tools only.
Internal search combines a skill filter with semantic search against Agent 5's profile.
Candidates from external sources are written back to the Candidate Database (with source and
consent status "none") before ranking, so every ranked candidate is a database record.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from recruiting.agents.ranking import CandidateRankingAgent
from recruiting.agents.resume_intake import ResumeIntake
from recruiting.ai.search import SemanticSearch
from recruiting.ai.skills import canonical
from recruiting.records.memory import CandidateRepository
from recruiting.records.models import Candidate, Job, Ranking


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
    pool: int = 0


def search_query(job: Job) -> str:
    analysis = job.analysis or {}
    return "\n".join(
        part
        for part in [
            job.title,
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
    ) -> None:
        self._candidates = candidates
        self._search = search
        self._intake = intake
        self._ranking = ranking
        self._sources = sources
        self._pool_size = pool_size

    def find(self, job: Job) -> tuple[list[Candidate], SearchReport]:
        report = SearchReport()
        query = search_query(job)
        must = [
            canonical(s).lower()
            for s in (job.analysis.get("must_have_skills") or job.required_skills)
        ]

        pool: dict[str, Candidate] = {}
        for hit in self._search.search("candidate", query, limit=self._pool_size):
            candidate = self._candidates.get(hit.id)
            if candidate:
                pool[candidate.id] = candidate
                report.internal_semantic += 1
        # Skill filter catches strong matches the lexical embedder ranks low.
        if must:
            for candidate in self._candidates.list(limit=10_000):
                if candidate.id not in pool and any(s.lower() in must for s in candidate.skills):
                    pool[candidate.id] = candidate
                    report.internal_skill += 1

        for source in self._sources:
            found = list(source.search(query, must, job.location, limit=50))
            report.external_found[source.name] = len(found)
            for resume in found:
                outcome = self._intake.add(resume.resume_text, source=resume.source)
                report.external_new += int(outcome.created)
                pool[outcome.candidate.id] = outcome.candidate

        report.pool = len(pool)
        return list(pool.values()), report

    def run(self, job: Job, limit: int = 20) -> tuple[Ranking, SearchReport]:
        candidates, report = self.find(job)
        return self._ranking.rank(job, candidates, limit=limit), report
