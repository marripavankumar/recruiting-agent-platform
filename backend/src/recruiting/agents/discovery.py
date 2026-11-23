"""Agent 1 - Company Discovery (BRD Agent 1, plan section 6).

Pulls company records from the active company-data sources, keeps those that match the search
criteria, de-duplicates by web domain, maps contact titles to the BRD contact roles and writes
everything to the Company Database with its source and date.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from recruiting.records.memory import CompanyRepository, new_id, normalize_domain
from recruiting.records.models import Company, ConsentBasis, Contact, ContactRole

# Q1: union of BRD section 3 and the Agent 1 list. Q3: USA and Canada at launch.
DEFAULT_INDUSTRIES = (
    "Manufacturing", "Automotive", "Medical Devices", "Industrial Equipment", "Aerospace",
    "Energy", "Consumer Products", "Electronics", "Heavy Equipment", "Defense", "Life Sciences",
    "Industrial Automation", "Oil & Gas", "Semiconductor",
)  # fmt: skip


@dataclass(frozen=True)
class DiscoveryCriteria:
    min_revenue_usd: float = 100_000_000
    countries: tuple[str, ...] = ("US", "CA")  # add "MX" in the later phase (Q3)
    industries: tuple[str, ...] = DEFAULT_INDUSTRIES


@dataclass(frozen=True)
class SourceContact:
    full_name: str
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    country: str | None = None


@dataclass(frozen=True)
class SourceCompany:
    """A company as one source reports it, before de-duplication."""

    name: str
    domain: str
    industry: str | None = None
    revenue_usd: float | None = None
    employees: int | None = None
    country: str | None = None
    locations: tuple[str, ...] = ()
    linkedin_url: str | None = None
    career_page_url: str | None = None
    contacts: tuple[SourceContact, ...] = ()


class CompanySource(Protocol):
    name: str

    def search(self, criteria: DiscoveryCriteria) -> Iterable[SourceCompany]: ...


# Title -> BRD contact role. First match wins, so the more specific patterns come first.
_ROLE_RULES: list[tuple[re.Pattern[str], ContactRole]] = [
    (
        re.compile(r"\b(director|head|vp|vice president)\b.*\bengineering\b", re.I),
        ContactRole.DIRECTOR_OF_ENGINEERING,
    ),
    (
        re.compile(r"\btalent acquisition\b|\bta (lead|partner|manager)\b", re.I),
        ContactRole.TALENT_ACQUISITION,
    ),
    (re.compile(r"\brecruit(er|ing)\b", re.I), ContactRole.RECRUITER),
    (re.compile(r"\b(hr|human resources|people)\b", re.I), ContactRole.HR_MANAGER),
    (
        re.compile(r"\bengineering manager\b|\bmanager\b.*\bengineering\b", re.I),
        ContactRole.ENGINEERING_MANAGER,
    ),
    (
        re.compile(r"\bhiring manager\b|\b(manager|supervisor|lead)\b", re.I),
        ContactRole.HIRING_MANAGER,
    ),
]


def contact_role(title: str | None) -> ContactRole:
    for pattern, role in _ROLE_RULES:
        if title and pattern.search(title):
            return role
    return ContactRole.OTHER


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def matches(company: SourceCompany, criteria: DiscoveryCriteria) -> str | None:
    """None if the company qualifies, otherwise the reason it was skipped."""
    if company.revenue_usd is None or company.revenue_usd < criteria.min_revenue_usd:
        return "revenue"
    if (company.country or "").upper() not in criteria.countries:
        return "country"
    wanted = {i.lower() for i in criteria.industries}
    if (company.industry or "").lower() not in wanted:
        return "industry"
    return None


@dataclass
class DiscoveryReport:
    seen: int = 0
    created: int = 0
    updated: int = 0
    contacts_added: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


class CompanyDiscoveryAgent:
    def __init__(
        self,
        companies: CompanyRepository,
        sources: Sequence[CompanySource],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._companies = companies
        self._sources = sources
        self._clock = clock

    def run(self, criteria: DiscoveryCriteria | None = None) -> DiscoveryReport:
        criteria = criteria or DiscoveryCriteria()
        report = DiscoveryReport()
        for source in self._sources:
            for found in source.search(criteria):
                report.seen += 1
                reason = matches(found, criteria)
                if reason:
                    report.skip(reason)
                    continue
                self._upsert(found, source.name, report)
        return report

    def _upsert(self, found: SourceCompany, source: str, report: DiscoveryReport) -> None:
        now = self._clock()
        domain = normalize_domain(found.domain)
        existing = self._companies.by_domain(domain)
        if existing is None:
            company = Company(id=new_id(), name=found.name.strip(), domain=domain, source=source)
            report.created += 1
        else:
            company = existing
            report.updated += 1
        # Newer data fills gaps and refreshes figures; it never clears a known value.
        company.industry = found.industry or company.industry
        company.revenue_usd = found.revenue_usd or company.revenue_usd
        company.employees = found.employees or company.employees
        company.country = (found.country or company.country or "").upper() or None
        company.locations = sorted({*company.locations, *found.locations})
        company.website = company.website or f"https://{domain}"
        company.linkedin_url = found.linkedin_url or company.linkedin_url
        company.career_page_url = found.career_page_url or company.career_page_url
        company.last_verified_at = now
        self._companies.save(company)

        known = {(c.email or "").lower() for c in self._companies.contacts(company.id)} - {""}
        for person in found.contacts:
            email = (person.email or "").strip().lower() or None
            if email and (email in known or not _EMAIL.match(email)):
                continue
            self._companies.save_contact(
                Contact(
                    id=new_id(),
                    company_id=company.id,
                    full_name=person.full_name.strip(),
                    title=person.title,
                    role=contact_role(person.title),
                    email=email,
                    phone=person.phone,
                    linkedin_url=person.linkedin_url,
                    country=(person.country or company.country or "").upper() or None,
                    # Consent is unknown until recorded; CASL blocks Canadian email until then.
                    consent_basis=ConsentBasis.NONE,
                    source=source,
                )
            )
            if email:
                known.add(email)
            report.contacts_added += 1


class CsvCompanySource:
    """Industry-database export or a purchased list, one row per contact.

    Columns: company, domain, industry, revenue_usd, employees, country, location, linkedin_url,
    career_page_url, contact_name, contact_title, contact_email, contact_phone,
    contact_linkedin_url. Rows of the same domain are merged.
    """

    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self._text = text

    def search(self, criteria: DiscoveryCriteria) -> Iterable[SourceCompany]:
        grouped: dict[str, dict] = {}
        for row in csv.DictReader(io.StringIO(self._text)):
            domain = normalize_domain(row.get("domain") or "")
            if not domain or not (row.get("company") or "").strip():
                continue
            entry = grouped.setdefault(domain, {"row": row, "contacts": [], "locations": set()})
            if row.get("location"):
                entry["locations"].add(row["location"].strip())
            if (row.get("contact_name") or "").strip():
                entry["contacts"].append(
                    SourceContact(
                        full_name=row["contact_name"],
                        title=row.get("contact_title") or None,
                        email=row.get("contact_email") or None,
                        phone=row.get("contact_phone") or None,
                        linkedin_url=row.get("contact_linkedin_url") or None,
                        country=row.get("country") or None,
                    )
                )
        for domain, entry in grouped.items():
            row = entry["row"]
            yield SourceCompany(
                name=row["company"],
                domain=domain,
                industry=row.get("industry") or None,
                revenue_usd=_number(row.get("revenue_usd")),
                employees=int(_number(row.get("employees")) or 0) or None,
                country=row.get("country") or None,
                locations=tuple(sorted(entry["locations"])),
                linkedin_url=row.get("linkedin_url") or None,
                career_page_url=row.get("career_page_url") or None,
                contacts=tuple(entry["contacts"]),
            )


def _number(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = re.sub(r"[,$\s]", "", value)
    multiplier = 1.0
    if cleaned[-1:].upper() in ("M", "B"):
        multiplier = 1e6 if cleaned[-1].upper() == "M" else 1e9
        cleaned = cleaned[:-1]
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None
