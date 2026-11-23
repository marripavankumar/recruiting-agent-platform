"""PostgreSQL repositories for the Company, Job and Candidate databases (migrations 0001, 0004)."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from recruiting.ai.llm import UsageRecord
from recruiting.db import Connect, is_uuid
from recruiting.records.memory import normalize_domain
from recruiting.records.models import (
    Candidate,
    Company,
    ConsentBasis,
    ConsentStatus,
    Contact,
    ContactRole,
    Job,
    JobPosting,
    JobStatus,
    OutreachStatus,
    Priority,
)

_COMPANY = """id::text, name, domain, source, industry, revenue_usd, employees, country, locations,
    website, linkedin_url, career_page_url, is_client, outreach_status, priority_score,
    hiring_history, last_verified_at"""
_CONTACT = """id::text, company_id::text, full_name, title, role_category, email, phone,
    linkedin_url, country, consent_basis, source"""
_POSTING = """id::text, company_id::text, title, target_role, url, location, hiring_manager,
    date_posted, first_seen, last_seen, active"""


def _company(row: tuple) -> Company:
    (cid, name, domain, source, industry, revenue, employees, country, locations, website,
     linkedin, career, is_client, status, score, history, verified) = row  # fmt: skip
    return Company(
        id=cid, name=name, domain=domain, source=source, industry=industry,
        revenue_usd=float(revenue) if revenue is not None else None, employees=employees,
        country=country.strip() if country else None, locations=list(locations or []),
        website=website, linkedin_url=linkedin, career_page_url=career, is_client=is_client,
        outreach_status=OutreachStatus(status), priority_score=score,
        hiring_history=list(history or []), last_verified_at=verified,
    )  # fmt: skip


def _contact(row: tuple) -> Contact:
    cid, company_id, name, title, role, email, phone, linkedin, country, consent, source = row
    return Contact(
        id=cid, company_id=company_id, full_name=name, title=title, role=ContactRole(role),
        email=email, phone=phone, linkedin_url=linkedin,
        country=country.strip() if country else None, consent_basis=ConsentBasis(consent),
        source=source,
    )  # fmt: skip


def _posting(row: tuple) -> JobPosting:
    return JobPosting(*row)


class PostgresCompanies:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def _one(self, sql: str, params: tuple) -> Company | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {_COMPANY} FROM company WHERE {sql}", params).fetchone()
        return _company(row) if row else None

    def by_domain(self, domain: str) -> Company | None:
        return self._one("domain = %s", (normalize_domain(domain),))

    def get(self, company_id: str) -> Company | None:
        return self._one("id = %s", (company_id,)) if is_uuid(company_id) else None

    def save(self, company: Company) -> Company:
        company.domain = normalize_domain(company.domain)
        values = (
            company.id, company.name, company.domain, company.source, company.industry,
            company.revenue_usd, company.employees, company.country, Jsonb(company.locations),
            company.website, company.linkedin_url, company.career_page_url, company.is_client,
            company.outreach_status.value, company.priority_score, Jsonb(company.hiring_history),
            company.last_verified_at,
        )  # fmt: skip
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO company (id, name, domain, source, industry, revenue_usd, employees,
                    country, locations, website, linkedin_url, career_page_url, is_client,
                    outreach_status, priority_score, hiring_history, last_verified_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name, domain = EXCLUDED.domain, industry = EXCLUDED.industry,
                    revenue_usd = EXCLUDED.revenue_usd, employees = EXCLUDED.employees,
                    country = EXCLUDED.country, locations = EXCLUDED.locations,
                    website = EXCLUDED.website, linkedin_url = EXCLUDED.linkedin_url,
                    career_page_url = EXCLUDED.career_page_url, is_client = EXCLUDED.is_client,
                    outreach_status = EXCLUDED.outreach_status,
                    priority_score = EXCLUDED.priority_score,
                    hiring_history = EXCLUDED.hiring_history,
                    last_verified_at = EXCLUDED.last_verified_at, updated_at = now()
                """,
                values,
            )
        return company

    def list(
        self, *, industry: str | None = None, min_priority: int | None = None, limit: int = 200
    ) -> list[Company]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COMPANY} FROM company
                WHERE (%(industry)s::text IS NULL OR industry = %(industry)s)
                  AND (%(min)s::int IS NULL OR priority_score >= %(min)s)
                ORDER BY priority_score DESC, lower(name) LIMIT %(limit)s
                """,
                {"industry": industry, "min": min_priority, "limit": limit},
            ).fetchall()
        return [_company(r) for r in rows]

    def set_outreach_status(self, company_id: str, status: OutreachStatus) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE company SET outreach_status = %s, updated_at = now() WHERE id = %s",
                (status.value, company_id),
            )

    def contacts(self, company_id: str) -> list[Contact]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_CONTACT} FROM company_contact WHERE company_id = %s ORDER BY created_at",
                (company_id,),
            ).fetchall()
        return [_contact(r) for r in rows]

    def save_contact(self, contact: Contact) -> Contact:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO company_contact (id, company_id, full_name, title, role_category, email,
                    phone, linkedin_url, country, consent_basis, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET full_name = EXCLUDED.full_name,
                    title = EXCLUDED.title, role_category = EXCLUDED.role_category,
                    email = EXCLUDED.email, phone = EXCLUDED.phone,
                    linkedin_url = EXCLUDED.linkedin_url, country = EXCLUDED.country,
                    consent_basis = EXCLUDED.consent_basis
                """,
                (
                    contact.id, contact.company_id, contact.full_name, contact.title,
                    contact.role.value, contact.email, contact.phone, contact.linkedin_url,
                    contact.country, contact.consent_basis.value, contact.source,
                ),
            )  # fmt: skip
        return contact

    def contact_by_email(self, email: str) -> Contact | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_CONTACT} FROM company_contact WHERE lower(email) = lower(%s)"
                " ORDER BY created_at LIMIT 1",
                (email.strip(),),
            ).fetchone()
        return _contact(row) if row else None

    def postings(self, company_id: str, *, active_only: bool = True) -> list[JobPosting]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_POSTING} FROM job_posting WHERE company_id = %s"
                " AND (active OR NOT %s) ORDER BY date_posted DESC NULLS LAST",
                (company_id, active_only),
            ).fetchall()
        return [_posting(r) for r in rows]

    def posting_by_url(self, url: str) -> JobPosting | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_POSTING} FROM job_posting WHERE url = %s", (url,)
            ).fetchone()
        return _posting(row) if row else None

    def save_posting(self, posting: JobPosting) -> JobPosting:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO job_posting (id, company_id, title, target_role, url, location,
                    hiring_manager, date_posted, first_seen, last_seen, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title,
                    target_role = EXCLUDED.target_role, location = EXCLUDED.location,
                    date_posted = EXCLUDED.date_posted, last_seen = EXCLUDED.last_seen,
                    active = EXCLUDED.active
                """,
                (
                    posting.id, posting.company_id, posting.title, posting.target_role, posting.url,
                    posting.location, posting.hiring_manager, posting.date_posted,
                    posting.first_seen, posting.last_seen, posting.active,
                ),
            )  # fmt: skip
        return posting


_JOB = """id::text, job_code, client_company_id::text, title, status, priority, recruiter_id::text,
    responsibilities, required_skills, preferred_skills, location, experience, visa_requirements,
    education, industry, travel, work_mode, salary, contract_type, source_email_ref, review_fields,
    analysis, created_at"""


def _job(row: tuple) -> Job:
    (jid, code, company, title, status, priority, recruiter, responsibilities, required, preferred,
     location, experience, visa, education, industry, travel, mode, salary, contract, source,
     review, analysis, created) = row  # fmt: skip
    return Job(
        id=jid, job_code=code, client_company_id=company, title=title, status=JobStatus(status),
        priority=Priority(priority), recruiter_id=recruiter, responsibilities=responsibilities,
        required_skills=list(required), preferred_skills=list(preferred), location=location,
        experience=experience, visa_requirements=visa, education=education, industry=industry,
        travel=travel, work_mode=mode, salary=salary, contract_type=contract,
        source_email_ref=source, review_fields=list(review), analysis=dict(analysis or {}),
        created_at=created,
    )  # fmt: skip


def _job_values(job: Job) -> dict[str, Any]:
    return {
        "id": job.id, "code": job.job_code, "company": job.client_company_id, "title": job.title,
        "status": job.status.value, "priority": job.priority.value,
        "recruiter": job.recruiter_id if job.recruiter_id and is_uuid(job.recruiter_id) else None,
        "responsibilities": job.responsibilities, "required": job.required_skills,
        "preferred": job.preferred_skills, "location": job.location, "experience": job.experience,
        "visa": job.visa_requirements, "education": job.education, "industry": job.industry,
        "travel": job.travel, "mode": job.work_mode, "salary": job.salary,
        "contract": job.contract_type, "source": job.source_email_ref, "review": job.review_fields,
        "analysis": Jsonb(job.analysis),
    }  # fmt: skip


class PostgresJobs:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def add(self, job: Job) -> Job:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO job (id, job_code, client_company_id, title, status, priority,
                    recruiter_id, responsibilities, required_skills, preferred_skills, location,
                    experience, visa_requirements, education, industry, travel, work_mode, salary,
                    contract_type, source_email_ref, review_fields, analysis)
                VALUES (%(id)s, %(code)s, %(company)s, %(title)s, %(status)s, %(priority)s,
                    %(recruiter)s, %(responsibilities)s, %(required)s, %(preferred)s, %(location)s,
                    %(experience)s, %(visa)s, %(education)s, %(industry)s, %(travel)s, %(mode)s,
                    %(salary)s, %(contract)s, %(source)s, %(review)s, %(analysis)s)
                """,
                _job_values(job),
            )
        return job

    def get(self, job_id: str) -> Job | None:
        if not is_uuid(job_id):
            return None
        with self._connect() as conn:
            row = conn.execute(f"SELECT {_JOB} FROM job WHERE id = %s", (job_id,)).fetchone()
        return _job(row) if row else None

    def save(self, job: Job) -> Job:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE job SET title = %(title)s, status = %(status)s, priority = %(priority)s,
                    recruiter_id = %(recruiter)s, responsibilities = %(responsibilities)s,
                    required_skills = %(required)s, preferred_skills = %(preferred)s,
                    location = %(location)s, experience = %(experience)s,
                    visa_requirements = %(visa)s, education = %(education)s,
                    industry = %(industry)s, travel = %(travel)s, work_mode = %(mode)s,
                    salary = %(salary)s, contract_type = %(contract)s,
                    review_fields = %(review)s, analysis = %(analysis)s, updated_at = now()
                WHERE id = %(id)s
                """,
                _job_values(job),
            )
        return job

    def list(
        self, *, client_company_id: str | None = None, status: JobStatus | None = None
    ) -> list[Job]:
        if client_company_id is not None and not is_uuid(client_company_id):
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {_JOB} FROM job
                WHERE (%(company)s::uuid IS NULL OR client_company_id = %(company)s::uuid)
                  AND (%(status)s::text IS NULL OR status = %(status)s)
                ORDER BY created_at DESC
                """,
                {"company": client_company_id, "status": status.value if status else None},
            ).fetchall()
        return [_job(r) for r in rows]

    def next_job_code(self, year: int) -> str:
        """Next free BRD Job ID. Two intakes racing may pick the same code; the UNIQUE
        constraint then rejects the second insert and the reply is processed again."""
        prefix = f"JOB-{year}-"
        with self._connect() as conn:
            (highest,) = conn.execute(
                "SELECT max(substring(job_code FROM %s)::int) FROM job WHERE job_code LIKE %s",
                (len(prefix) + 1, f"{prefix}%"),
            ).fetchone()
        return f"{prefix}{(highest or 0) + 1:04d}"


_CANDIDATE = """id::text, full_name, source, email, phone, linkedin_url, current_location,
    preferred_location, skills, projects, experience_years, availability, notice_period,
    visa_status, work_authorization, expected_rate, resume_text, consent_status"""


class PostgresCandidates:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    @staticmethod
    def _candidate(row: tuple) -> Candidate:
        values = list(row)
        values[8] = list(values[8] or [])
        values[9] = list(values[9] or [])
        values[10] = float(values[10]) if values[10] is not None else None
        values[17] = ConsentStatus(values[17])
        return Candidate(*values)

    def add(self, candidate: Candidate) -> Candidate:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO candidate (id, full_name, source, email, phone, linkedin_url,
                    current_location, preferred_location, skills, projects, experience_years,
                    availability, notice_period, visa_status, work_authorization, expected_rate,
                    resume_text, consent_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    candidate.id, candidate.full_name, candidate.source, candidate.email,
                    candidate.phone, candidate.linkedin_url, candidate.current_location,
                    candidate.preferred_location, candidate.skills, Jsonb(candidate.projects),
                    candidate.experience_years, candidate.availability, candidate.notice_period,
                    candidate.visa_status, candidate.work_authorization, candidate.expected_rate,
                    candidate.resume_text, candidate.consent_status.value,
                ),
            )  # fmt: skip
        return candidate

    def get(self, candidate_id: str) -> Candidate | None:
        if not is_uuid(candidate_id):
            return None
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_CANDIDATE} FROM candidate WHERE id = %s", (candidate_id,)
            ).fetchone()
        return self._candidate(row) if row else None

    def list(self, limit: int = 200) -> list[Candidate]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_CANDIDATE} FROM candidate ORDER BY created_at DESC LIMIT %s", (limit,)
            ).fetchall()
        return [self._candidate(r) for r in rows]


class PostgresUsageLedger:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def record(self, entry: UsageRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_usage (template_key, provider, model, input_tokens, output_tokens,
                    cost_usd, at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    entry.template_key, entry.provider, entry.model, entry.input_tokens,
                    entry.output_tokens, entry.cost_usd, entry.at,
                ),
            )  # fmt: skip

    def entries(self) -> list[UsageRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT template_key, provider, model, input_tokens, output_tokens, cost_usd, at
                FROM llm_usage WHERE at > now() - interval '90 days' ORDER BY at
                """
            ).fetchall()
        return [
            UsageRecord(t, p, m, i, o, float(c) if c is not None else None, at)
            for t, p, m, i, o, c, at in rows
        ]
