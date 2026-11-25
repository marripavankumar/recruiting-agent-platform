"""Affinda resume parser adapter (stack decision: default resume parser; BRD section 8).

Calls the v3 API, `POST /v3/documents` (multipart), and maps the parsed resume onto the same
ParsedResume the Claude parser returns, so the rest of resume intake does not change.

- The platform reads uploaded files to text first (documents.py), so the adapter sends that text
  as a .txt file: Affinda takes files or URLs, not plain text.
- `wait=true` and `deleteAfterParse=true`: Affinda answers when parsing is done and keeps no copy
  of the resume. The file name and identifiers sent carry no personal data.
- The API key is a secret (RAP_SECRET_AFFINDA_API_KEY with the environment provider), read on
  each call so a rotated key works without a restart. It only works on the region where it was
  created: https://api.affinda.com (global/AU), https://api.us1.affinda.com (US),
  https://api.eu1.affinda.com (EU).
- Affinda documents its compact field shapes only in part, so values are read leniently: a field
  may be a plain value or an object with parsed/raw parts.

Any failure (HTTP error, rate limit, parsing error) raises ResumeParserError; resume intake then
falls back to the Claude parser and the rules, so a resume is never lost.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import httpx

from recruiting.ai.prompts import ParsedResume, WorkRole

TokenProvider = Callable[[], str]
_YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")
_VALUE_KEYS = ("parsed", "value", "formatted", "name", "label", "url", "raw")


class ResumeParserError(Exception):
    pass


def _text(value: Any) -> str | None:
    """A plain string from a value that may be a string, number, list or {parsed|raw|...}."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return next((t for t in (_text(v) for v in value) if t), None)
    if isinstance(value, dict):
        for key in _VALUE_KEYS:
            if key in value:
                found = _text(value[key])
                if found:
                    return found
    return None


def _texts(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [t for t in (_text(v) for v in items) if t]


def _parsed(value: Any) -> Any:
    """Unwrap {parsed: ...} (non-compact responses); compact values pass through."""
    return value.get("parsed", value) if isinstance(value, dict) else value


def _name(value: Any) -> str | None:
    name = _parsed(value)
    if isinstance(name, dict):
        parts = [
            _text(name.get(k)) for k in (
                "candidateNameFirst", "candidateNameMiddle", "candidateNameFamily",
                "first", "middle", "last",
            )
        ]  # fmt: skip
        joined = " ".join(p for p in parts if p)
        if joined:
            return joined
    return _text(value)


def _location(value: Any) -> str | None:
    loc = _parsed(value)
    if isinstance(loc, dict):
        city, state = _text(loc.get("city")), _text(loc.get("state"))
        if city or state:
            return ", ".join(p for p in (city, state) if p)
        return _text(loc.get("formatted")) or _text(loc.get("rawInput"))
    return _text(value)


def _years(value: Any) -> tuple[int | None, int | None]:
    """(start, end) from a date range; end None when the role is current."""
    if value is None:
        return None, None
    dump = json.dumps(value, default=str).lower()
    years = sorted(int(y) for y in _YEAR.findall(dump))
    if not years:
        return None, None
    flagged = '"iscurrent"' in dump
    current = '"iscurrent": true' in dump or (not flagged and "present" in dump)
    end = None if current else (years[-1] if len(years) > 1 else None)
    return years[0], end


def _work(items: Any) -> list[WorkRole]:
    roles = []
    for item in items if isinstance(items, list) else []:
        item = _parsed(item)
        if not isinstance(item, dict):
            continue
        title = _text(item.get("jobTitle"))
        if not title:
            continue
        start, end = _years(item.get("workExperienceDateRange"))
        company = _text(item.get("workExperienceOrganization"))
        roles.append(WorkRole(title=title[:200], company=company, start_year=start, end_year=end))
    return roles


def _education(items: Any) -> str | None:
    for item in items if isinstance(items, list) else []:
        item = _parsed(item)
        if not isinstance(item, dict):
            continue
        degree = _text(item.get("educationAccreditation"))
        major = ", ".join(_texts(item.get("educationMajor")))
        text = f"{degree} ({major})" if degree and major else (degree or major)
        if text:
            return text[:120]
    return None


def _linkedin(websites: Any) -> str | None:
    return next((w for w in _texts(websites) if "linkedin.com/" in w.lower()), None)


def to_parsed_resume(data: dict[str, Any]) -> ParsedResume:
    years = _text(data.get("totalYearsExperience"))
    try:
        experience = float(years) if years is not None else None
    except ValueError:
        experience = None
    return ParsedResume(
        full_name=(_name(data.get("candidateName")) or "Unknown")[:120],
        email=next(iter(_texts(data.get("email"))), None),
        phone=next(iter(_texts(data.get("phoneNumber"))), None),
        linkedin_url=_linkedin(data.get("website")),
        current_location=_location(data.get("location")),
        skills=list(dict.fromkeys(_texts(data.get("skill")))),
        experience_years=experience,
        education=_education(data.get("education")),
        industries=[],
        work_history=_work(data.get("workExperience")),
        projects=[],
        work_authorization=_text(data.get("rightToWork")),
        availability=_text(data.get("availability")),
    )


class AffindaResumeParser:
    name = "affinda"

    def __init__(
        self,
        api_key: TokenProvider,
        workspace: str,
        document_type: str | None = None,
        base_url: str = "https://api.affinda.com",
        client: httpx.Client | None = None,
    ) -> None:
        if not workspace:
            raise ValueError("Affinda needs a workspace id (RAP_AFFINDA_WORKSPACE)")
        self._key = api_key
        self._workspace = workspace
        self._document_type = document_type
        self._http = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=90)

    def parse(self, text: str) -> ParsedResume:
        form = {
            "workspace": self._workspace,
            "wait": "true",
            "deleteAfterParse": "true",  # Affinda keeps no copy
            "compact": "true",
            "fileName": "resume.txt",
        }
        if self._document_type:
            form["documentType"] = self._document_type
        try:
            response = self._http.post(
                "/v3/documents",
                data=form,
                files={"file": ("resume.txt", text.encode("utf-8"), "text/plain")},
                headers={"Authorization": f"Bearer {self._key()}"},
            )
        except httpx.HTTPError as exc:
            raise ResumeParserError(f"Affinda not reachable: {type(exc).__name__}") from exc
        if response.status_code == 429:
            raise ResumeParserError("Affinda rate limit reached (429)")
        if response.status_code >= 400:
            # The body is not echoed: it may quote the resume.
            raise ResumeParserError(f"Affinda answered HTTP {response.status_code}")
        try:
            document = response.json()
        except ValueError as exc:
            raise ResumeParserError("Affinda answered with something other than JSON") from exc
        meta = document.get("meta") or {}
        error = document.get("error") or {}
        code = meta.get("errorCode") or (
            error.get("errorCode") if isinstance(error, dict) else None
        )
        if meta.get("failed") or code:
            raise ResumeParserError(f"Affinda could not parse the resume ({code or 'failed'})")
        data = document.get("data")
        if not isinstance(data, dict) or not data:
            raise ResumeParserError("Affinda returned no resume data (not ready)")
        return to_parsed_resume(data)
