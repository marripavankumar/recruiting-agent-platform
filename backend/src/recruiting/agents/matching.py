"""Agent 10 - Resume Matching (BRD Agent 10, plan section 10).

Compares a candidate's parsed resume with the job's Agent 5 profile in three layers:
1. the structured match from the ranking model (skills, experience, location, availability ...);
2. embedding similarity between the whole resume and the job description;
3. an LLM review that reads the JD and the resume together (rule-based text if no LLM).

It produces the BRD outputs: match score, strengths, missing skills, interview probability and
client submission notes. A missing must-have skill or a work-authorisation problem is flagged,
never silently dropped: the recruiter decides (compliance checklist F5, scenario S17).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

from recruiting.agents.jd_analysis import job_text
from recruiting.agents.ranking import NO_SPONSORSHIP_JOB, CandidateRankingAgent, needs_sponsorship
from recruiting.ai.llm import LLMError, LLMGateway
from recruiting.ai.prompts import MatchReview
from recruiting.ai.search import Embedder, HashingEmbedder, relatedness
from recruiting.ai.skills import canonical
from recruiting.records.models import Candidate, Job

STRUCTURED_SHARE = 0.85  # the rest of the score is resume-to-JD embedding similarity


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def resume_text(candidate: Candidate) -> str:
    if (candidate.resume_text or "").strip():
        return candidate.resume_text or ""
    roles = "; ".join(f"{r.get('title')} at {r.get('company')}" for r in candidate.work_history)
    projects = "; ".join(f"{p.get('title')}: {p.get('description')}" for p in candidate.projects)
    return "\n".join(
        x for x in (", ".join(candidate.skills), roles, projects, candidate.education or "") if x
    )


def interview_probability(score: int, must_cov: float, blocked: bool) -> float:
    """Estimate (0..1) until Agent 12 outcomes allow calibration: a logistic curve on the
    score, halved for each missing half of the must-have skills, low if a hard rule fails."""
    p = 0.8 / (1 + math.exp(-(score - 65) / 8))
    p *= 0.5 + 0.5 * must_cov
    if blocked:
        p *= 0.3
    return round(p, 2)


class ResumeMatchingAgent:
    def __init__(
        self,
        ranking: CandidateRankingAgent,
        gateway: LLMGateway | None = None,
        embedder: Embedder | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self._ranking = ranking
        self._gateway = gateway
        self._embedder = embedder or HashingEmbedder()
        self._on_error = on_error or (lambda _key, _exc: None)

    def _skill_matrix(self, job: Job, candidate: Candidate) -> tuple[list[dict], list[str]]:
        must = [canonical(s) for s in job.analysis.get("must_have_skills") or job.required_skills]
        nice = [
            canonical(s) for s in job.analysis.get("nice_to_have_skills") or job.preferred_skills
        ]
        have = {s.lower() for s in candidate.skills}
        text = resume_text(candidate).lower()
        matrix, missing = [], []
        for level, skills in (("must_have", must), ("nice_to_have", nice)):
            for skill in dict.fromkeys(skills):
                has = skill.lower() in have
                matrix.append(
                    {
                        "skill": skill,
                        "level": level,
                        "has": has,
                        "in_resume_text": skill.lower() in text,
                    }
                )
                if level == "must_have" and not has:
                    missing.append(skill)
        return matrix, missing

    def _key_projects(self, job: Job, candidate: Candidate) -> list[str]:
        if not candidate.projects:
            return []
        texts = [f"{p.get('title', '')}. {p.get('description', '')}" for p in candidate.projects]
        vectors = self._embedder.embed([job_text(job), *texts])
        ranked = sorted(
            zip(texts, vectors[1:], strict=True), key=lambda tv: -_cosine(vectors[0], tv[1])
        )
        return [t.strip(" .") for t, _ in ranked[:3]]

    def match(self, job: Job, candidate: Candidate) -> dict[str, Any]:
        structured = self._ranking.score(job, candidate)
        matrix, missing = self._skill_matrix(job, candidate)
        must_total = sum(1 for m in matrix if m["level"] == "must_have")
        must_cov = 1 - len(missing) / must_total if must_total else 1.0

        jd_vec, cv_vec = self._embedder.embed([job_text(job), resume_text(candidate)])
        similarity = relatedness(self._embedder, jd_vec, cv_vec)
        score = round(
            STRUCTURED_SHARE * structured.score + (1 - STRUCTURED_SHARE) * 100 * similarity
        )

        flags: list[dict[str, str]] = []

        def flag(code: str, message: str) -> None:
            flags.append({"code": code, "message": message})

        if missing:
            flag("missing_must_have", f"Missing must-have skills: {', '.join(missing)}")
        sponsorship = needs_sponsorship(candidate)
        if job.visa_requirements and NO_SPONSORSHIP_JOB.search(job.visa_requirements):
            if sponsorship:
                flag(
                    "work_authorization",
                    f"Needs visa sponsorship; job requires: {job.visa_requirements}",
                )
            elif sponsorship is None:
                flag("work_authorization_unknown", "Work authorisation not confirmed")
        if structured.excluded_reason and not any(
            f["code"] in ("missing_must_have", "work_authorization") for f in flags
        ):
            flag("hard_filter", structured.excluded_reason)
        blocked = any(f["code"] in ("missing_must_have", "work_authorization") for f in flags)

        report: dict[str, Any] = {
            "score": score,
            "structured_score": structured.score,
            "jd_similarity": round(similarity, 3),
            "components": structured.components,
            "strengths": list(structured.strengths),
            "concerns": list(structured.gaps),
            "missing_skills": missing,
            "skill_matrix": matrix,
            "interview_probability": interview_probability(score, must_cov, blocked),
            "flags": flags,
            "key_projects": self._key_projects(job, candidate),
            "summary": self._rule_summary(candidate, must_total - len(missing), must_total),
            "notes": "",
            "source": "rules",
        }
        report["notes"] = self._rule_notes(job, candidate, report)

        if self._gateway is not None:
            try:
                review = self._gateway.run(
                    "match_review",
                    MatchReview,
                    title=job.title,
                    job_code=job.job_code,
                    industry=job.industry or "not stated",
                    location=job.location or "not stated",
                    must=", ".join(m["skill"] for m in matrix if m["level"] == "must_have"),
                    nice=", ".join(m["skill"] for m in matrix if m["level"] == "nice_to_have"),
                    jd=job_text(job),
                    structured=json.dumps(
                        {k: report[k] for k in ("structured_score", "missing_skills", "flags")}
                    ),
                    resume=resume_text(candidate)[:30_000],
                )
            except LLMError as exc:
                self._on_error(f"match:{job.id}:{candidate.id}", exc)
            else:
                report.update(
                    strengths=review.strengths[:5] or report["strengths"],
                    # Platform-detected gaps stay visible even if the review omits them.
                    concerns=list(dict.fromkeys([*review.concerns[:5], *structured.gaps])),
                    key_projects=review.key_projects[:3] or report["key_projects"],
                    summary=review.summary.strip() or report["summary"],
                    notes=review.submission_notes.strip() or report["notes"],
                    source="llm",
                )
        return report

    @staticmethod
    def _rule_summary(candidate: Candidate, must_hit: int, must_total: int) -> str:
        parts = []
        if candidate.work_history:
            latest = candidate.work_history[0]
            role = latest.get("title") or "Engineer"
            where = f" at {latest['company']}" if latest.get("company") else ""
            now = "currently" if latest.get("end_year") is None else "most recently"
            parts.append(f"{candidate.full_name} is {now} {role}{where}.")
        else:
            parts.append(f"{candidate.full_name}.")
        if candidate.experience_years is not None:
            parts.append(f"{candidate.experience_years:g} years of engineering experience.")
        if must_total:
            parts.append(f"Has {must_hit} of {must_total} must-have skills for this role.")
        if candidate.education:
            parts.append(f"{candidate.education}.")
        return " ".join(parts)

    @staticmethod
    def _rule_notes(job: Job, candidate: Candidate, report: dict[str, Any]) -> str:
        have = [m["skill"] for m in report["skill_matrix"] if m["has"]][:5]
        lines = []
        if have:
            lines.append(f"Hands-on with {', '.join(have)}.")
        facts = [
            f"available {candidate.availability}" if candidate.availability else None,
            candidate.work_authorization,
            f"based in {candidate.current_location}" if candidate.current_location else None,
        ]
        if any(facts):
            text = ", ".join(f for f in facts if f)
            lines.append(text[0].upper() + text[1:] + ".")
        if report["key_projects"]:
            lines.append(f"Relevant work: {report['key_projects'][0]}.")
        return " ".join(lines)
