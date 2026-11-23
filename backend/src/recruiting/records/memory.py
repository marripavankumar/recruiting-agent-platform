"""In-memory repositories for tests and quick local runs. Single-process use only."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterable
from dataclasses import replace
from typing import Protocol

from recruiting.records.models import (
    Candidate,
    Company,
    Contact,
    Job,
    JobPosting,
    JobStatus,
    OutreachStatus,
)


def new_id() -> str:
    return str(uuid.uuid4())


def normalize_domain(value: str) -> str:
    """ "https://www.Northfield-Axle.com/careers" -> "northfield-axle.com"."""
    value = value.strip().lower()
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    value = value.split("/", 1)[0].split("?", 1)[0]
    return value[4:] if value.startswith("www.") else value


class CompanyRepository(Protocol):
    def by_domain(self, domain: str) -> Company | None: ...

    def get(self, company_id: str) -> Company | None: ...

    def save(self, company: Company) -> Company:
        """Insert or update by id."""
        ...

    def list(
        self, *, industry: str | None = None, min_priority: int | None = None, limit: int = 200
    ) -> list[Company]: ...

    def set_outreach_status(self, company_id: str, status: OutreachStatus) -> None: ...

    def contacts(self, company_id: str) -> list[Contact]: ...

    def save_contact(self, contact: Contact) -> Contact: ...

    def contact_by_email(self, email: str) -> Contact | None: ...

    def postings(self, company_id: str, *, active_only: bool = True) -> list[JobPosting]: ...

    def posting_by_url(self, url: str) -> JobPosting | None: ...

    def save_posting(self, posting: JobPosting) -> JobPosting: ...


class JobRepository(Protocol):
    def add(self, job: Job) -> Job: ...

    def get(self, job_id: str) -> Job | None: ...

    def save(self, job: Job) -> Job: ...

    def list(
        self, *, client_company_id: str | None = None, status: JobStatus | None = None
    ) -> list[Job]: ...

    def next_job_code(self, year: int) -> str: ...


class CandidateRepository(Protocol):
    def add(self, candidate: Candidate) -> Candidate: ...

    def get(self, candidate_id: str) -> Candidate | None: ...

    def list(self, limit: int = 200) -> list[Candidate]: ...


class InMemoryCompanies:
    def __init__(self) -> None:
        self._companies: dict[str, Company] = {}
        self._contacts: dict[str, Contact] = {}
        self._postings: dict[str, JobPosting] = {}

    def by_domain(self, domain: str) -> Company | None:
        domain = normalize_domain(domain)
        return next(
            (copy.deepcopy(c) for c in self._companies.values() if c.domain == domain), None
        )

    def get(self, company_id: str) -> Company | None:
        company = self._companies.get(company_id)
        return copy.deepcopy(company) if company else None

    def save(self, company: Company) -> Company:
        company = replace(company, domain=normalize_domain(company.domain))
        other = self.by_domain(company.domain)
        if other is not None and other.id != company.id:
            raise ValueError(f"Domain {company.domain} already belongs to company {other.id}")
        self._companies[company.id] = copy.deepcopy(company)
        return company

    def list(
        self, *, industry: str | None = None, min_priority: int | None = None, limit: int = 200
    ) -> list[Company]:
        rows = [
            c
            for c in self._companies.values()
            if (industry is None or c.industry == industry)
            and (min_priority is None or c.priority_score >= min_priority)
        ]
        rows.sort(key=lambda c: (-c.priority_score, c.name.lower()))
        return [copy.deepcopy(c) for c in rows[:limit]]

    def set_outreach_status(self, company_id: str, status: OutreachStatus) -> None:
        self._companies[company_id].outreach_status = status

    def contacts(self, company_id: str) -> list[Contact]:
        return [copy.deepcopy(c) for c in self._contacts.values() if c.company_id == company_id]

    def save_contact(self, contact: Contact) -> Contact:
        self._contacts[contact.id] = copy.deepcopy(contact)
        return contact

    def contact_by_email(self, email: str) -> Contact | None:
        wanted = email.strip().lower()
        return next(
            (
                copy.deepcopy(c)
                for c in self._contacts.values()
                if (c.email or "").lower() == wanted
            ),
            None,
        )

    def postings(self, company_id: str, *, active_only: bool = True) -> list[JobPosting]:
        return [
            copy.deepcopy(p)
            for p in self._postings.values()
            if p.company_id == company_id and (p.active or not active_only)
        ]

    def posting_by_url(self, url: str) -> JobPosting | None:
        return next((copy.deepcopy(p) for p in self._postings.values() if p.url == url), None)

    def save_posting(self, posting: JobPosting) -> JobPosting:
        self._postings[posting.id] = copy.deepcopy(posting)
        return posting


class InMemoryJobs:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def add(self, job: Job) -> Job:
        if any(j.job_code == job.job_code for j in self._jobs.values()):
            raise ValueError(f"Job code {job.job_code} already exists")
        self._jobs[job.id] = copy.deepcopy(job)
        return job

    def get(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        return copy.deepcopy(job) if job else None

    def save(self, job: Job) -> Job:
        self._jobs[job.id] = copy.deepcopy(job)
        return job

    def list(
        self, *, client_company_id: str | None = None, status: JobStatus | None = None
    ) -> list[Job]:
        return [
            copy.deepcopy(j)
            for j in self._jobs.values()
            if (client_company_id is None or j.client_company_id == client_company_id)
            and (status is None or j.status == status)
        ]

    def next_job_code(self, year: int) -> str:
        prefix = f"JOB-{year}-"
        numbers = [
            int(j.job_code[len(prefix) :])
            for j in self._jobs.values()
            if j.job_code.startswith(prefix)
        ]
        return f"{prefix}{max(numbers, default=0) + 1:04d}"


class InMemoryCandidates:
    def __init__(self, candidates: Iterable[Candidate] = ()) -> None:
        self._candidates = {c.id: copy.deepcopy(c) for c in candidates}

    def add(self, candidate: Candidate) -> Candidate:
        self._candidates[candidate.id] = copy.deepcopy(candidate)
        return candidate

    def get(self, candidate_id: str) -> Candidate | None:
        candidate = self._candidates.get(candidate_id)
        return copy.deepcopy(candidate) if candidate else None

    def list(self, limit: int = 200) -> list[Candidate]:
        return [copy.deepcopy(c) for c in list(self._candidates.values())[:limit]]
