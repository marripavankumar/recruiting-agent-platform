"""Resume intake: turns resume text into a Candidate Database record (BRD section 8).

Used by Agent 6 for the internal Resume Database and by recruiters adding resumes by hand;
Agent 9 will use it for resumes collected from candidates. Parsing goes through the resume
parser adapter (Affinda) when one is configured, then the LLM gateway, then the rules: each one
is the fallback for the one before. Candidates are de-duplicated by email, phone and LinkedIn
URL, and indexed for semantic search.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

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
from recruiting.storage import DocumentStore, store_resume

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"(\+?\(?\d[\d\s().-]{8,}\d)")  # "(313) 555-0108", "+1 313 555 0108"
_LINKEDIN = re.compile(r"(https?://)?(www\.)?linkedin\.com/in/[\w%-]+/?", re.I)
_YEARS = re.compile(r"(\d{1,2}(?:\.\d)?)\+?\s*(?:years|yrs)", re.I)
_DEGREE = re.compile(
    r"\b(Ph\.?D|M\.?S\.?|M\.?Sc|M\.?Eng|MBA|B\.?S\.?|B\.?Sc|B\.?E\.?|B\.?Tech)\b[^\n,;]*", re.I
)


# "|", bullet, middle dot, en dash, em dash or tab between the name and the contact details.
_SEPARATORS = re.compile(r"\s*(?:\||•|·|–|—|\t)\s*")


def name_from_first_line(line: str) -> str:
    """Resume headers often read "Name | email | phone": keep the part before the contacts."""
    first = _SEPARATORS.split(line, maxsplit=1)[0].strip()
    if _EMAIL.search(first) or _PHONE.search(first):
        return "Unknown"
    return first[:120] or "Unknown"


def parse_by_rules(text: str) -> ParsedResume:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    email = _EMAIL.search(text)
    phone = _PHONE.search(text)
    linkedin = _LINKEDIN.search(text)
    years = [float(m) for m in _YEARS.findall(text)]
    degree = _DEGREE.search(text)
    return ParsedResume(
        full_name=name_from_first_line(lines[0]) if lines else "Unknown",
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


class ResumeParser(Protocol):
    name: str

    def parse(self, text: str) -> ParsedResume:
        """Raises an exception when it cannot parse; intake then falls back."""
        ...


@dataclass(frozen=True)
class ResumeFile:
    """An uploaded resume file, kept as sent when a document store is configured."""

    filename: str
    data: bytes
    kind: str  # documents.extract_text's kind: "pdf", "docx" or "text"


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
        parser: ResumeParser | None = None,
        documents: DocumentStore | None = None,
    ) -> None:
        self._parser = parser
        self._documents = documents
        self._candidates = candidates
        self._search = search
        self._gateway = gateway
        self._on_error = on_error or (lambda _key, _exc: None)

    def parse(self, text: str) -> tuple[ParsedResume, str]:
        if self._parser is not None:
            try:
                return self._parser.parse(text), self._parser.name
            except Exception as exc:  # any parser failure: fall back, never lose the resume
                self._on_error("resume_parser", exc)
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
        candidate_id: str | None = None,
        file: ResumeFile | None = None,
    ) -> IntakeOutcome:
        """With `candidate_id` (a resume the candidate uploaded), that record is updated. With
        `file`, the original is kept in the document store and replaces any earlier one."""
        parsed, by = self.parse(resume_text)
        if candidate_id is not None:
            existing = self._candidates.get(candidate_id)
        else:
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
        replaced = self._keep_file(candidate, file)
        self._candidates.save(candidate)
        if replaced and self._documents is not None:
            self._documents.delete(replaced)  # only after the record points at the new file
        if self._search is not None:
            self._search.index(
                "candidate", candidate.id, candidate_text(candidate), {"source": candidate.source}
            )
        return IntakeOutcome(candidate, existing is None, by)

    def _keep_file(self, candidate: Candidate, file: ResumeFile | None) -> str | None:
        """Store the original; the key of the file it replaces, if any. A storage failure keeps
        the resume text and is logged: the candidate is never lost over a file."""
        if file is None or self._documents is None:
            return None
        try:
            stored = store_resume(
                self._documents, candidate.id, file.filename, file.data, file.kind
            )
        except Exception as exc:
            self._on_error("resume_file", exc)
            return None
        old = (candidate.resume_file or {}).get("key")
        candidate.resume_file = stored.to_dict()
        return old if old != stored.key else None
