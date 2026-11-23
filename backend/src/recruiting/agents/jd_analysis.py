"""Agent 5 - JD Analysis (BRD Agent 5, plan section 7).

Turns a job record into a structured search profile (feeds Agent 6) and indexes it for
semantic search (feeds Agents 6 and 10). Skill names are normalised through the shared skill
dictionary so JDs and resumes use the same vocabulary. Missing must-have facts (location, visa,
rate) are flagged so the Account Manager can ask the client.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from recruiting.ai.llm import LLMError, LLMGateway
from recruiting.ai.prompts import JDAnalysis
from recruiting.ai.search import SemanticSearch
from recruiting.ai.skills import canonical, extract_skills
from recruiting.records.models import Job

# Job fields the client must supply before candidates can be submitted.
MUST_HAVE_FACTS = {"location": "location", "visa_requirements": "visa", "salary": "rate"}


def _unique(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        if item and item.lower() not in {k.lower() for k in seen}:
            seen[item] = None
    return list(seen)


def job_text(job: Job, raw_text: str = "") -> str:
    parts = [
        job.title,
        job.responsibilities or "",
        "Required: " + ", ".join(job.required_skills),
        "Preferred: " + ", ".join(job.preferred_skills),
        job.experience or "",
        job.education or "",
        job.industry or "",
        raw_text,
    ]
    return "\n".join(p for p in parts if p)


class JDAnalysisAgent:
    def __init__(
        self,
        search: SemanticSearch | None = None,
        gateway: LLMGateway | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self._search = search
        self._gateway = gateway
        self._on_error = on_error or (lambda _key, _exc: None)

    def analyse(self, job: Job, raw_text: str = "") -> Job:
        text = job_text(job, raw_text)
        by_category: dict[str, list[str]] = {}
        for match in extract_skills(text):
            by_category.setdefault(match.category, []).append(match.skill)

        llm: JDAnalysis | None = None
        if self._gateway is not None:
            try:
                llm = self._gateway.run(
                    "jd_analysis",
                    JDAnalysis,
                    title=job.title,
                    industry=job.industry or "unknown",
                    text=text,
                )
            except LLMError as exc:
                self._on_error(job.id, exc)

        if llm is not None:
            must = [canonical(s) for s in llm.must_have_skills]
            nice = [canonical(s) for s in llm.nice_to_have_skills]
            for category in (
                "software",
                "cad_tools",
                "manufacturing_methods",
                "erp",
                "plm",
                "automation",
                "programming",
            ):
                by_category[category] = _unique(
                    by_category.get(category, []) + [canonical(s) for s in getattr(llm, category)]
                )
            years = llm.years_experience_min
            summary = llm.search_summary
            lean, six_sigma = llm.lean, llm.six_sigma
        else:
            must = [canonical(s) for s in job.required_skills] or [
                s for skills in by_category.values() for s in skills
            ]
            nice = [canonical(s) for s in job.preferred_skills]
            match = re.search(r"(\d+)\s*\+?\s*(?:years|yrs)", job.experience or text, re.I)
            years = int(match.group(1)) if match else None
            summary = f"{job.title}; must have {', '.join(must[:5]) or 'skills to confirm'}."
            lean = "Lean Manufacturing" in by_category.get("lean", [])
            six_sigma = "Six Sigma" in by_category.get("six_sigma", [])

        missing = [label for attr, label in MUST_HAVE_FACTS.items() if not getattr(job, attr)]
        job.analysis = {
            "must_have_skills": _unique(must),
            "nice_to_have_skills": [s for s in _unique(nice) if s not in must],
            "years_experience_min": years,
            "skills_by_category": by_category,
            "lean": lean,
            "six_sigma": six_sigma,
            "search_summary": summary,
            "missing_info": missing,
            "analysed_by": "llm" if llm is not None else "rules",
        }
        job.review_fields = _unique(
            job.review_fields + [f for f in MUST_HAVE_FACTS if not getattr(job, f)]
        )

        if self._search is not None:
            self._search.index(
                "job",
                job.id,
                f"{text}\n{summary}",
                {
                    "client_company_id": job.client_company_id,
                    "status": job.status.value,
                    "job_code": job.job_code,
                },
            )
        return job
