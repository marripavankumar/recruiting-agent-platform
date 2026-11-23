"""Agent 7 - Candidate Ranking (BRD Agent 7, plan section 8).

Scores each candidate 0-100 from the ten BRD components with Administrator-set weights,
applies the hard filters first (must-have skills, work authorisation), and explains every
rank in plain language. Scoring is rule-based so each point can be traced; no model sees names,
photos or other protected attributes (plan section 8, fairness).

Unknown facts score a neutral 0.5 and are listed as gaps for the recruiter to check, so a thin
profile is neither rewarded nor wiped out.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from recruiting.agents.hiring_signals import target_role
from recruiting.ai.search import Embedder, HashingEmbedder
from recruiting.ai.skills import canonical
from recruiting.records.models import Candidate, Job, RankedCandidate, Ranking

NEUTRAL = 0.5


@dataclass(frozen=True)
class RankingWeights:
    """Plan section 8 starting weights; the Administrator can change them (sum need not be 1)."""

    skill_match: float = 0.25
    industry_match: float = 0.10
    years_experience: float = 0.10
    project_similarity: float = 0.10
    recent_experience: float = 0.10
    location: float = 0.08
    availability: float = 0.08
    education: float = 0.07
    resume_quality: float = 0.07
    communication: float = 0.05
    # Hard filter: share of must-have skills a candidate needs to be ranked at all.
    min_must_have_coverage: float = 0.5

    def components(self) -> dict[str, float]:
        values = asdict(self)
        values.pop("min_must_have_coverage")
        return values

    def validate(self) -> RankingWeights:
        if any(v < 0 for v in asdict(self).values()):
            raise ValueError("Weights cannot be negative")
        if sum(self.components().values()) <= 0:
            raise ValueError("At least one weight must be above zero")
        if not 0 <= self.min_must_have_coverage <= 1:
            raise ValueError("min_must_have_coverage must be between 0 and 1")
        return self


@dataclass(frozen=True)
class CommunicationWeights:
    """Q9: configurable rules for the Communication Score."""

    reply_speed: float = 0.4  # 1 when replying within a day, 0 after five days
    completeness: float = 0.3  # share of requested details the candidate supplied
    recruiter_rating: float = 0.3  # 1-5 stars from the recruiter


# ----- helpers -------------------------------------------------------------------------------

_SPONSORSHIP_NEEDED = re.compile(
    r"\b(h-?1b|tn\b|opt\b|cpt\b|needs? sponsorship|requires? sponsorship|visa sponsorship)", re.I
)
_SPONSORSHIP_FREE = re.compile(
    r"\b(us citizen|u\.s\. citizen|citizen|green card|permanent resident|gc holder|ead)\b", re.I
)
NO_SPONSORSHIP_JOB = re.compile(
    r"\b(citizen|green card|no sponsorship|without sponsorship|clearance)\b", re.I
)
_DEGREES = [
    (re.compile(r"\bph\.?d\b|\bdoctor", re.I), 4),
    (re.compile(r"\bm\.?s\b|\bm\.?sc\b|\bm\.?eng\b|\bmaster", re.I), 3),
    (re.compile(r"\bb\.?s\b|\bb\.?sc\b|\bb\.?e\b|\bb\.?tech\b|\bbachelor", re.I), 2),
    (re.compile(r"\bdiploma\b|\bassociate", re.I), 1),
]
_US_STATES = re.compile(r",\s*([A-Z]{2})\b")


def _degree_level(text: str | None) -> int | None:
    for pattern, level in _DEGREES:
        if text and pattern.search(text):
            return level
    return None


def _city_state(location: str | None) -> tuple[str | None, str | None]:
    if not location:
        return None, None
    city = location.split(",")[0].strip().lower() or None
    state = _US_STATES.search(location)
    return city, state.group(1) if state else None


def _weeks_until_available(text: str | None) -> float | None:
    if not text:
        return None
    lowered = text.lower()
    if re.search(r"immediate|now|asap|available today", lowered):
        return 0
    match = re.search(r"(\d+)\s*(day|week|month)", lowered)
    if not match:
        return None
    number, unit = int(match.group(1)), match.group(2)
    return number / 7 if unit == "day" else number * 4.3 if unit == "month" else number


def needs_sponsorship(candidate: Candidate) -> bool | None:
    text = " ".join(filter(None, [candidate.work_authorization, candidate.visa_status]))
    if not text:
        return None
    if _SPONSORSHIP_NEEDED.search(text):
        return True
    if _SPONSORSHIP_FREE.search(text):
        return False
    return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


# ----- the agent -----------------------------------------------------------------------------


class CandidateRankingAgent:
    def __init__(
        self,
        weights: Callable[[], RankingWeights] = RankingWeights,
        communication: CommunicationWeights | None = None,
        embedder: Embedder | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._weights = weights
        self._communication = communication or CommunicationWeights()
        self._embedder = embedder or HashingEmbedder()
        self._clock = clock

    # Each component returns (value 0..1, strength or None, gap or None).

    def _skills(self, job: Job, have: set[str]) -> tuple[float, list[str], list[str], float]:
        must = [canonical(s) for s in job.analysis.get("must_have_skills") or job.required_skills]
        nice = [
            canonical(s) for s in job.analysis.get("nice_to_have_skills") or job.preferred_skills
        ]
        lower = {s.lower() for s in have}
        must_hit = [s for s in must if s.lower() in lower]
        nice_hit = [s for s in nice if s.lower() in lower]
        must_cov = len(must_hit) / len(must) if must else 1.0
        nice_cov = len(nice_hit) / len(nice) if nice else must_cov
        strengths, gaps = [], []
        if must:
            listed = ", ".join(must_hit[:4]) or "none"
            strengths.append(f"Has {len(must_hit)} of {len(must)} must-have skills ({listed})")
            missing = [s for s in must if s not in must_hit]
            if missing:
                gaps.append(f"Missing must-have: {', '.join(missing[:4])}")
        if nice_hit:
            strengths.append(f"Also has {', '.join(nice_hit[:3])}")
        return 0.75 * must_cov + 0.25 * nice_cov, strengths, gaps, must_cov

    def score(self, job: Job, candidate: Candidate) -> RankedCandidate:
        weights = self._weights().validate()
        strengths: list[str] = []
        gaps: list[str] = []
        c: dict[str, float] = {}

        skill, s_str, s_gap, must_cov = self._skills(job, set(candidate.skills))
        c["skill_match"], strengths, gaps = skill, strengths + s_str, gaps + s_gap

        industry = (job.industry or "").lower()
        if not industry or not candidate.industries:
            c["industry_match"] = NEUTRAL
        else:
            hit = any(industry in i.lower() or i.lower() in industry for i in candidate.industries)
            c["industry_match"] = 1.0 if hit else 0.2
            if hit:
                strengths.append(f"{job.industry} industry experience")

        required = job.analysis.get("years_experience_min")
        years = candidate.experience_years
        if years is None:
            c["years_experience"] = NEUTRAL
            gaps.append("Years of experience unknown")
        elif not required:
            c["years_experience"] = min(years / 3, 1.0)
        else:
            c["years_experience"] = min(years / required, 1.0)
            (strengths if years >= required else gaps).append(
                f"{years:g} years vs {required} required"
            )

        project_text = " ".join(
            f"{p.get('title', '')} {p.get('description', '')}" for p in candidate.projects
        )
        if project_text.strip():
            job_text = " ".join(
                [job.title, job.responsibilities or "", " ".join(job.required_skills)]
            )
            a, b = self._embedder.embed([job_text, project_text])
            # Lexical cosine rarely exceeds ~0.6 even for close matches; rescale to 0..1.
            c["project_similarity"] = min(_cosine(a, b) / 0.5, 1.0)
            if c["project_similarity"] >= 0.6:
                strengths.append("Past projects close to this role")
        else:
            c["project_similarity"] = NEUTRAL

        if not candidate.work_history:
            c["recent_experience"] = NEUTRAL
        else:
            latest = candidate.work_history[0]
            end = latest.get("end_year")
            age = 0 if end is None else max(self._clock().year - int(end), 0)
            recency = 1.0 if age == 0 else 0.7 if age <= 2 else 0.4 if age <= 5 else 0.1
            job_role = target_role(job.title)
            same_role = job_role is not None and target_role(latest.get("title", "")) == job_role
            c["recent_experience"] = min(recency * (1.0 if same_role else 0.7), 1.0)
            if same_role and recency >= 0.7:
                strengths.append(f"Currently or recently a {latest.get('title')}")

        job_city, job_state = _city_state(job.location)
        cand_city, cand_state = _city_state(candidate.current_location)
        preferred = (candidate.preferred_location or "").lower()
        if job.work_mode == "remote":
            c["location"] = 1.0
        elif not job.location or not candidate.current_location:
            c["location"] = NEUTRAL
            if not candidate.current_location:
                gaps.append("Location unknown")
        elif job_city and cand_city == job_city:
            c["location"] = 1.0
            strengths.append(f"Based in {candidate.current_location}")
        elif job_state and cand_state == job_state:
            c["location"] = 0.8
            strengths.append(f"Same state ({candidate.current_location})")
        elif job_city and (
            job_city in preferred or "anywhere" in preferred or "relocat" in preferred
        ):
            c["location"] = 0.7
            strengths.append("Willing to relocate")
        else:
            c["location"] = 0.2
            gaps.append(f"Located in {candidate.current_location}, job in {job.location}")

        weeks = _weeks_until_available(candidate.availability)
        if weeks is None:
            c["availability"] = NEUTRAL
            gaps.append("Availability unknown")
        else:
            c["availability"] = (
                1.0
                if weeks <= 0.5
                else 0.9
                if weeks <= 2
                else 0.7
                if weeks <= 4
                else 0.4
                if weeks <= 8
                else 0.2
            )
            if weeks <= 2:
                strengths.append(f"Available {candidate.availability}")

        needed = _degree_level(job.education)
        have = _degree_level(candidate.education)
        if needed is None:
            c["education"] = 1.0 if have else NEUTRAL
        elif have is None:
            c["education"] = NEUTRAL
        else:
            c["education"] = 1.0 if have >= needed else 0.5
            if have < needed:
                gaps.append(f"Education below the stated requirement ({candidate.education})")

        filled = [
            bool(candidate.email), bool(candidate.phone), len(candidate.skills) >= 5,
            bool(candidate.work_history), bool(candidate.projects),
            candidate.experience_years is not None, bool(candidate.education),
        ]  # fmt: skip
        c["resume_quality"] = sum(filled) / len(filled)

        comm = candidate.communication or {}
        cw = self._communication
        parts: list[tuple[float, float]] = []
        if comm.get("reply_hours") is not None:
            parts.append(
                (cw.reply_speed, max(0.0, min(1.0, 1 - (float(comm["reply_hours"]) - 24) / 96)))
            )
        if comm.get("completeness") is not None:
            parts.append((cw.completeness, max(0.0, min(1.0, float(comm["completeness"])))))
        if comm.get("recruiter_rating") is not None:
            parts.append((cw.recruiter_rating, (float(comm["recruiter_rating"]) - 1) / 4))
        total = sum(w for w, _ in parts)
        c["communication"] = sum(w * v for w, v in parts) / total if total else NEUTRAL

        # Hard filters (plan section 8): must-have skills and work authorisation.
        excluded = None
        if must_cov < weights.min_must_have_coverage:
            excluded = f"Has under {weights.min_must_have_coverage:.0%} of the must-have skills"
        sponsorship = needs_sponsorship(candidate)
        if job.visa_requirements and NO_SPONSORSHIP_JOB.search(job.visa_requirements):
            if sponsorship:
                excluded = (
                    excluded or f"Needs visa sponsorship; job requires: {job.visa_requirements}"
                )
            elif sponsorship is None:
                gaps.append("Confirm work authorisation")

        component_weights = weights.components()
        total_weight = sum(component_weights.values())
        score = round(
            100 * sum(component_weights[k] * c[k] for k in component_weights) / total_weight
        )
        return RankedCandidate(
            candidate_id=candidate.id,
            score=score,
            components={k: round(v, 3) for k, v in c.items()},
            strengths=strengths[:8],
            gaps=gaps[:5],
            excluded_reason=excluded,
        )

    def rank(self, job: Job, candidates: Iterable[Candidate], limit: int = 20) -> Ranking:
        scored = [self.score(job, c) for c in candidates]
        eligible = sorted((r for r in scored if r.excluded_reason is None), key=lambda r: -r.score)
        for position, result in enumerate(eligible[:limit], start=1):
            result.rank = position
        excluded = [r for r in scored if r.excluded_reason is not None]
        return Ranking(
            job_id=job.id,
            ranked_at=self._clock(),
            weights=self._weights().components(),
            candidates=eligible[:limit] + excluded,
            searched=len(scored),
        )
