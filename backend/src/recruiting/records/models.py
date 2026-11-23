"""Company, Job and Candidate records (BRD section 8, plan section 5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class OutreachStatus(StrEnum):
    NOT_CONTACTED = "not_contacted"
    CONTACTED = "contacted"
    REPLIED = "replied"
    CLIENT = "client"
    DO_NOT_CONTACT = "do_not_contact"


class ContactRole(StrEnum):
    """Agent 1 output: the contact roles the BRD asks for."""

    HIRING_MANAGER = "hiring_manager"
    ENGINEERING_MANAGER = "engineering_manager"
    HR_MANAGER = "hr_manager"
    TALENT_ACQUISITION = "talent_acquisition"
    DIRECTOR_OF_ENGINEERING = "director_of_engineering"
    RECRUITER = "recruiter"
    OTHER = "other"


class ConsentBasis(StrEnum):
    """CASL consent basis for commercial email (compliance checklist E8, E9)."""

    NONE = "none"
    EXPRESS = "express"
    IMPLIED_CONSPICUOUS_PUBLICATION = "implied_conspicuous_publication"
    IMPLIED_EXISTING_RELATIONSHIP = "implied_existing_relationship"
    NOT_REQUIRED = "not_required"


@dataclass
class Contact:
    id: str
    company_id: str
    full_name: str
    title: str | None
    role: ContactRole
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    country: str | None = None  # ISO 3166 alpha-2
    consent_basis: ConsentBasis = ConsentBasis.NONE
    source: str = "manual"


@dataclass
class Company:
    id: str
    name: str
    domain: str  # de-duplication key, e.g. "northfield-axle.com"
    source: str
    industry: str | None = None
    revenue_usd: float | None = None
    employees: int | None = None
    country: str | None = None
    locations: list[str] = field(default_factory=list)
    website: str | None = None
    linkedin_url: str | None = None
    career_page_url: str | None = None
    is_client: bool = False
    outreach_status: OutreachStatus = OutreachStatus.NOT_CONTACTED
    priority_score: int = 0
    hiring_history: list[dict[str, Any]] = field(default_factory=list)
    last_verified_at: datetime | None = None


@dataclass
class JobPosting:
    """Agent 2 output: a public posting that signals the company is hiring engineers."""

    id: str
    company_id: str
    title: str
    target_role: str | None  # one of the BRD section 4 positions, or None if not a target
    url: str
    location: str | None = None
    hiring_manager: str | None = None
    date_posted: date | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    active: bool = True


class JobStatus(StrEnum):
    OPEN = "open"
    ON_HOLD = "on_hold"
    FILLED = "filled"
    CLOSED = "closed"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class Job:
    """Job Database record (BRD section 8) with the Agent 4 fields and Agent 5 analysis."""

    id: str
    job_code: str  # the BRD "Job ID", e.g. JOB-2026-0142
    client_company_id: str
    title: str
    status: JobStatus = JobStatus.OPEN
    priority: Priority = Priority.MEDIUM
    recruiter_id: str | None = None
    responsibilities: str | None = None
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    location: str | None = None
    experience: str | None = None
    visa_requirements: str | None = None
    education: str | None = None
    industry: str | None = None
    travel: str | None = None
    work_mode: str | None = None  # onsite | remote | hybrid
    salary: str | None = None
    contract_type: str | None = None
    source_email_ref: str | None = None
    # Fields Agent 4 was unsure about, for the Account Manager to confirm.
    review_fields: list[str] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)  # Agent 5 search profile
    created_at: datetime | None = None


class ConsentStatus(StrEnum):
    NONE = "none"
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


@dataclass
class Candidate:
    """Candidate Database record (BRD section 8)."""

    id: str
    full_name: str
    source: str
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    current_location: str | None = None
    preferred_location: str | None = None
    skills: list[str] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    experience_years: float | None = None
    availability: str | None = None
    notice_period: str | None = None
    visa_status: str | None = None
    work_authorization: str | None = None
    expected_rate: str | None = None
    resume_text: str | None = None
    consent_status: ConsentStatus = ConsentStatus.NONE
