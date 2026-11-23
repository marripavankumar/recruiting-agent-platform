"""Resume intake: turns resume text into a Candidate Database record (BRD section 8).

Used by Agent 6 for the internal Resume Database and by recruiters adding resumes by hand;
Agent 9 will use it for resumes collected from candidates. Parsing goes through the LLM gateway
with a rule-based fallback. Candidates are de-duplicated by email, phone and LinkedIn URL, and
indexed for semantic search.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from recruiting.ai.llm import LLMError, LLMGateway
from recruiting.ai.prompts import ParsedResume
from recruiting.ai.search import SemanticSearch
from recruiting.ai.skills import canonical, extract_skills
from recruiting.records.memory import (
    CandidateRepository,
    new_id,
    normalize_linkedin,
    normalize_phone,
)
from recruiting.records.models import Candidate, ConsentStatus

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"(\+?\d[\d\s().-]{8,}\d)")
_LINKEDIN = re.compile(r"(https?://)?(www\.)?linkedin\.com/in/[\w%-]+/?", re.I)
_YEARS = re.compile(r"(\d{1,2}(?:\.\d)?)\+?\s*(?:years|yrs)", re.I)
_DEGREE = re.compile(
    r"\b(Ph\.?D|M\.?S\.?|M\.?Sc|M\.?Eng|MBA|B\.?S\.?|B\.?Sc|B\.?E\.?|B\.?Tech)\b[^\n,;]*", re.I
)


def parse_by_rules(text: str) -> ParsedResume:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    email = _EMAIL.search(text)
    phone = _PHONE.search(text)
    linkedin = _LINKEDIN.search(text)
    years = [float(m) for m in _YEARS.findall(text)]
    degree = _DEGREE.search(text)
    return ParsedResume(
        full_name=lines[0][:120] if lines else "Unknown",
        email=email.group(0) if email else None,
        phone=phone.group(0).strip() if phone else None,
        linkedin_url=linkedin.group(0) if linkedin else None,
        current_location=None,
        skills=[m.skill for m in extract_skills(text)],
        experience_years=max(years) if years else None,
        education=degree.group(0).strip()[:120] if degree else None,
        industries=[],
        work_history=[],
        projects=[],
        work_authorization=None,
        availability=None,
    )


def candidate_text(candidate: Candidate) -> str:
    """What semantic search sees. The name is left out: it must not influence matching."""
    roles = "; ".join(r.get("title", "") for r in candidate.work_history[:3])
    projects = " ".join(
        f"{p.get('title', '')} {p.get('description', '')}" for p in candidate.projects
    )
    return "\n".join(
        part
        for part in [
            roles,
            ", ".join(candidate.skills),
            ", ".join(candidate.industries),
            candidate.education or "",
            projects,
        ]
        if part
    )


@dataclass
class IntakeOutcome:
    candidate: Candidate
    created: bool
    parsed_by: str


class ResumeIntake:
    def __init__(
        self,
        candidates: CandidateRepository,
        search: SemanticSearch | None = None,
        gateway: LLMGateway | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self._candidates = candidates
        self._search = search
        self._gateway = gateway
        self._on_error = on_error or (lambda _key, _exc: None)

    def parse(self, text: str) -> tuple[ParsedResume, str]:
        if self._gateway is not None:
            try:
                return self._gateway.run("resume_parse", ParsedResume, resume=text), "llm"
            except LLMError as exc:
                self._on_error("resume", exc)
        return parse_by_rules(text), "rules"

    def add(
        self,
        resume_text: str,
        source: str,
        consent: ConsentStatus = ConsentStatus.NONE,
    ) -> IntakeOutcome:
        parsed, by = self.parse(resume_text)
        existing = self._candidates.find_duplicate(
            parsed.email, normalize_phone(parsed.phone), normalize_linkedin(parsed.linkedin_url)
        )
        candidate = existing or Candidate(id=new_id(), full_name=parsed.full_name, source=source)
        # A newer resume refreshes what it states and never clears known values.
        candidate.full_name = parsed.full_name or candidate.full_name
        candidate.email = (parsed.email or candidate.email or "").lower() or None
        candidate.phone = parsed.phone or candidate.phone
        candidate.linkedin_url = parsed.linkedin_url or candidate.linkedin_url
        candidate.current_location = parsed.current_location or candidate.current_location
        skills = [canonical(s) for s in parsed.skills] + [
            m.skill for m in extract_skills(resume_text)
        ]
        candidate.skills = list(dict.fromkeys([*skills, *candidate.skills]))
        candidate.experience_years = parsed.experience_years or candidate.experience_years
        candidate.education = parsed.education or candidate.education
        candidate.industries = list(dict.fromkeys([*parsed.industries, *candidate.industries]))
        if parsed.work_history:
            candidate.work_history = [r.model_dump() for r in parsed.work_history]
        if parsed.projects:
            candidate.projects = [p.model_dump() for p in parsed.projects]
        candidate.work_authorization = parsed.work_authorization or candidate.work_authorization
        candidate.availability = parsed.availability or candidate.availability
        candidate.resume_text = resume_text
        if consent is not ConsentStatus.NONE:
            candidate.consent_status = consent
        self._candidates.save(candidate)
        if self._search is not None:
            self._search.index(
                "candidate", candidate.id, candidate_text(candidate), {"source": candidate.source}
            )
        return IntakeOutcome(candidate, existing is None, by)
