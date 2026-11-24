"""Agent 2 - Hiring Opportunity Discovery (BRD Agent 2, plan section 6).

Reads each company's career page, keeps postings for the 27 target positions (BRD section 4,
Q2), links them to the Company Database (Hiring History), expires stale ones and gives each
company a 0-100 priority score from configurable weights (Q10).

Postings are read from schema.org JobPosting data (JSON-LD), which most career sites publish for
search engines, so no site-specific scraping is needed. robots.txt is respected.
"""

from __future__ import annotations

import json
import re
import urllib.robotparser
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from recruiting.records.memory import CompanyRepository, new_id
from recruiting.records.models import Company, JobPosting

# BRD section 4, in order. Patterns are tried most specific first (see _ROLE_PATTERNS).
TARGET_ROLES = (
    "Manufacturing Engineer", "Manufacturing Process Engineer", "Industrial Engineer",
    "Mechanical Engineer", "Electrical Engineer", "Electronics Engineer", "Controls Engineer",
    "Automation Engineer", "Quality Engineer", "Supplier Quality Engineer", "Design Engineer",
    "Product Engineer", "CAE Engineer", "FEA Engineer", "CFD Engineer", "Validation Engineer",
    "Reliability Engineer", "Test Engineer", "Production Engineer", "Tooling Engineer",
    "Robotics Engineer", "PLC Engineer", "Manufacturing Supervisor", "Engineering Manager",
    "Program Manager", "NPI Engineer", "Launch Engineer",
)  # fmt: skip

# The 10 roles BRD Agent 2 lists explicitly count as the strongest fit.
CORE_ROLES = frozenset({
    "Manufacturing Engineer", "Quality Engineer", "Mechanical Engineer", "CAE Engineer",
    "Electrical Engineer", "Design Engineer", "Industrial Engineer", "Product Engineer",
    "Manufacturing Supervisor", "Engineering Manager",
})  # fmt: skip

_ROLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (role, re.compile(pattern, re.I))
    for role, pattern in [
        ("Supplier Quality Engineer", r"\bsupplier quality\b|\bsqe\b"),
        ("Manufacturing Process Engineer", r"\bmanufacturing process\b|\bprocess engineer\b"),
        ("Manufacturing Supervisor", r"\b(manufacturing|production) supervisor\b"),
        ("Engineering Manager", r"\bengineering manager\b|\bmanager,? engineering\b"),
        ("Program Manager", r"\bprogram manager\b"),
        ("NPI Engineer", r"\bnpi\b|\bnew product introduction\b"),
        ("Launch Engineer", r"\blaunch engineer\b"),
        ("CAE Engineer", r"\bcae\b|\bcrash\b.*\bengineer\b|\bsimulation engineer\b"),
        ("FEA Engineer", r"\bfea\b|\bstress (engineer|analyst)\b"),
        ("CFD Engineer", r"\bcfd\b"),
        ("PLC Engineer", r"\bplc\b"),
        ("Robotics Engineer", r"\brobotics?\b"),
        ("Controls Engineer", r"\bcontrols?\b"),
        ("Automation Engineer", r"\bautomation\b"),
        ("Validation Engineer", r"\bvalidation\b"),
        ("Reliability Engineer", r"\breliability\b"),
        ("Test Engineer", r"\btest\b"),
        ("Tooling Engineer", r"\btooling\b"),
        ("Production Engineer", r"\bproduction engineer\b"),
        ("Electronics Engineer", r"\belectronics?\b"),
        ("Electrical Engineer", r"\belectrical\b"),
        ("Quality Engineer", r"\bquality\b"),
        ("Industrial Engineer", r"\bindustrial engineer\b"),
        ("Design Engineer", r"\bdesign engineer\b"),
        ("Product Engineer", r"\bproduct engineer\b|\bproduct development engineer\b"),
        ("Mechanical Engineer", r"\bmechanical\b"),
        ("Manufacturing Engineer", r"\bmanufacturing engineer\b|\bmfg engineer\b"),
    ]
]


def target_role(title: str) -> str | None:
    """Normalise a posting title to a BRD section 4 position, or None if it is not a target."""
    if not re.search(r"\b(engineer|engineering|manager|supervisor|analyst)\b", title, re.I):
        return None
    for role, pattern in _ROLE_PATTERNS:
        if pattern.search(title):
            return role
    return None


# ----- JobPosting extraction ---------------------------------------------------------------


@dataclass(frozen=True)
class FoundPosting:
    title: str
    url: str
    location: str | None
    date_posted: date | None
    hiring_manager: str | None = None


class _JsonLdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self._inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self._inside = True
            self.blocks.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._inside:
            self.blocks[-1] += data


def _walk(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "JobPosting" in types:
            yield node
        yield from _walk(node.get("@graph", []))
        yield from _walk(node.get("itemListElement", []))
        if isinstance(node.get("item"), dict):
            yield from _walk(node["item"])


def _location(posting: dict[str, Any]) -> str | None:
    places = posting.get("jobLocation")
    places = places if isinstance(places, list) else [places]
    for place in places:
        address = (place or {}).get("address") if isinstance(place, dict) else None
        if isinstance(address, dict):
            parts = [address.get("addressLocality"), address.get("addressRegion")]
            text = ", ".join(p for p in parts if p)
            if text:
                return text
    if posting.get("jobLocationType") == "TELECOMMUTE":
        return "Remote"
    return None


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def extract_postings(html: str, page_url: str) -> list[FoundPosting]:
    collector = _JsonLdCollector()
    collector.feed(html)
    found: dict[str, FoundPosting] = {}
    for block in collector.blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for posting in _walk(data):
            title = str(posting.get("title") or "").strip()
            if not title:
                continue
            url = urljoin(page_url, str(posting.get("url") or page_url))
            key = url if posting.get("url") else f"{url}#{title}"
            found[key] = FoundPosting(
                title, key, _location(posting), _date(posting.get("datePosted"))
            )
    return list(found.values())


# ----- Fetching ------------------------------------------------------------------------------


class CareerPageFetcher(Protocol):
    def fetch(self, url: str) -> str | None:
        """Page HTML, or None if it may not or could not be fetched."""
        ...


class HttpCareerPageFetcher:
    USER_AGENT = "RecruitingPlatformBot/0.1"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._http = client or httpx.Client(
            timeout=20, follow_redirects=True, headers={"User-Agent": self.USER_AGENT}
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                response = self._http.get(f"{origin}/robots.txt")
                parser.parse(response.text.splitlines() if response.status_code == 200 else [])
                self._robots[origin] = parser
            except httpx.HTTPError:
                self._robots[origin] = None
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(self.USER_AGENT, url)

    def fetch(self, url: str) -> str | None:
        if not self._allowed(url):
            return None
        try:
            response = self._http.get(url)
        except httpx.HTTPError:
            return None
        return response.text if response.status_code == 200 else None


# ----- Scoring -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PriorityWeights:
    """Q10: configurable weighted rules. Each factor is 0..1; the score is their weighted mean."""

    recency: float = 0.4  # newest posting age: 1 at <= 7 days, 0 at >= max_age_days
    volume: float = 0.35  # active target postings, saturating at volume_cap
    role_fit: float = 0.25  # share of postings that are core BRD Agent 2 roles
    volume_cap: int = 5
    max_age_days: int = 60


def priority_score(postings: list[JobPosting], today: date, weights: PriorityWeights) -> int:
    active = [p for p in postings if p.active and p.target_role]
    if not active:
        return 0
    ages = [(today - p.date_posted).days for p in active if p.date_posted]
    newest = min(ages) if ages else weights.max_age_days / 2
    recency = 1.0 if newest <= 7 else max(0.0, 1 - (newest - 7) / (weights.max_age_days - 7))
    volume = min(len(active), weights.volume_cap) / weights.volume_cap
    fit = sum(1 for p in active if p.target_role in CORE_ROLES) / len(active)
    total = weights.recency + weights.volume + weights.role_fit
    return round(
        100 * (weights.recency * recency + weights.volume * volume + weights.role_fit * fit) / total
    )


# ----- Agent ---------------------------------------------------------------------------------


@dataclass
class SignalReport:
    companies_checked: int = 0
    pages_unavailable: int = 0
    postings_found: int = 0
    target_postings: int = 0
    new_postings: int = 0
    expired: int = 0
    not_due: int = 0  # S4: checked recently enough, skipped by run_due
    scores: dict[str, int] = field(default_factory=dict)


class HiringSignalsAgent:
    def __init__(
        self,
        companies: CompanyRepository,
        fetcher: CareerPageFetcher,
        weights: PriorityWeights | None = None,
        stale_after_days: int = 21,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        recheck_hiring_days: int = 7,
        recheck_idle_days: int = 14,
    ) -> None:
        # S4: companies with open target roles are re-read weekly; the rest every two weeks,
        # so a company with no engineering jobs today is picked up when it starts hiring.
        self._recheck_hiring = timedelta(days=recheck_hiring_days)
        self._recheck_idle = timedelta(days=recheck_idle_days)
        self._companies = companies
        self._fetcher = fetcher
        self._weights = weights or PriorityWeights()
        self._stale_after = timedelta(days=stale_after_days)
        self._clock = clock

    def run(self, companies: Iterable[Company] | None = None) -> SignalReport:
        report = SignalReport()
        for company in companies if companies is not None else self._companies.list(limit=10_000):
            if company.career_page_url:
                self.check(company, report)
        return report

    @staticmethod
    def last_checked(company: Company) -> datetime | None:
        if not company.hiring_history:
            return None
        return datetime.fromisoformat(company.hiring_history[-1]["at"])

    def next_check_at(self, company: Company) -> datetime | None:
        """S4: when the career page is due to be read again (None: due now)."""
        last = self.last_checked(company)
        if last is None:
            return None
        return last + (self._recheck_hiring if company.priority_score > 0 else self._recheck_idle)

    def run_due(self) -> SignalReport:
        """Scheduled sweep (S4): re-check only the companies whose interval has passed."""
        report = SignalReport()
        now = self._clock()
        for company in self._companies.list(limit=1_000_000):
            if not company.career_page_url:
                continue
            due = self.next_check_at(company)
            if due is not None and due > now:
                report.not_due += 1
                continue
            self.check(company, report)
        return report

    def check(self, company: Company, report: SignalReport | None = None) -> int:
        report = report or SignalReport()
        report.companies_checked += 1
        now = self._clock()
        html = self._fetcher.fetch(company.career_page_url or "")
        if html is None:
            report.pages_unavailable += 1
        else:
            for found in extract_postings(html, company.career_page_url or ""):
                report.postings_found += 1
                role = target_role(found.title)
                if role is None:
                    continue
                report.target_postings += 1
                posting = self._companies.posting_by_url(found.url)
                if posting is None:
                    posting = JobPosting(
                        id=new_id(), company_id=company.id, title=found.title, target_role=role,
                        url=found.url, location=found.location, date_posted=found.date_posted,
                        first_seen=now,
                    )  # fmt: skip
                    report.new_postings += 1
                posting.last_seen = now
                posting.active = True
                self._companies.save_posting(posting)

        # Expire postings not seen recently or older than the scoring window.
        for posting in self._companies.postings(company.id):
            too_old = (
                posting.date_posted
                and (now.date() - posting.date_posted).days > self._weights.max_age_days
            )
            unseen = posting.last_seen and now - posting.last_seen > self._stale_after
            if too_old or unseen:
                posting.active = False
                self._companies.save_posting(posting)
                report.expired += 1

        active = self._companies.postings(company.id)
        score = priority_score(active, now.date(), self._weights)
        fresh = self._companies.get(company.id) or company
        fresh.priority_score = score
        fresh.hiring_history = [
            *fresh.hiring_history[-23:],  # keep about two years of monthly-ish snapshots
            {
                "at": now.isoformat(),
                "open_target_roles": len(active),
                "roles": sorted({p.target_role for p in active if p.target_role}),
            },
        ]
        self._companies.save(fresh)
        report.scores[company.id] = score
        return score
