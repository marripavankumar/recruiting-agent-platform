"""PostgreSQL repositories for the Company, Job and Candidate databases (migrations 0001, 0004)."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from recruiting.agents.ranking import RankingWeights
from recruiting.ai.llm import UsageRecord
from recruiting.db import Connect, is_uuid
from recruiting.records.followups import DEFAULT_CADENCE, validate_cadence
from recruiting.records.memory import normalize_domain
from recruiting.records.models import (
    Candidate,
    ClientDecision,
    Company,
    ConsentBasis,
    ConsentStatus,
    Contact,
    ContactRole,
    Engagement,
    EngagementStatus,
    FollowUp,
    FollowUpKind,
    FollowUpStatus,
    Job,
    JobPosting,
    JobStatus,
    OutreachStatus,
    PlacementStage,
    Priority,
    RankedCandidate,
    Ranking,
    Submission,
    SubmissionStatus,
    Verdict,
)
from recruiting.records.rankings import (
    UnknownRankingEntry,
    apply_move,
    carry_over_reviews,
    weights_dict,
)

_COMPANY = """id::text, name, domain, source, industry, revenue_usd, employees, country, locations,
    website, linkedin_url, career_page_url, is_client, outreach_status, priority_score,
    hiring_history, last_verified_at, created_at, sources, jd_promised_at"""
_CONTACT = """id::text, company_id::text, full_name, title, role_category, email, phone,
    linkedin_url, country, consent_basis, source, email_valid"""
_POSTING = """id::text, company_id::text, title, target_role, url, location, hiring_manager,
    date_posted, first_seen, last_seen, active"""


def _company(row: tuple) -> Company:
    (cid, name, domain, source, industry, revenue, employees, country, locations, website,
     linkedin, career, is_client, status, score, history, verified, created, sources,
     promised) = row  # fmt: skip
    return Company(
        id=cid, name=name, domain=domain, source=source, industry=industry,
        revenue_usd=float(revenue) if revenue is not None else None, employees=employees,
        country=country.strip() if country else None, locations=list(locations or []),
        website=website, linkedin_url=linkedin, career_page_url=career, is_client=is_client,
        outreach_status=OutreachStatus(status), priority_score=score,
        hiring_history=list(history or []), last_verified_at=verified, created_at=created,
        sources=list(sources or []), jd_promised_at=promised,
    )  # fmt: skip


def _contact(row: tuple) -> Contact:
    (cid, company_id, name, title, role, email, phone, linkedin, country, consent, source,
     valid) = row  # fmt: skip
    return Contact(
        id=cid, company_id=company_id, full_name=name, title=title, role=ContactRole(role),
        email=email, phone=phone, linkedin_url=linkedin,
        country=country.strip() if country else None, consent_basis=ConsentBasis(consent),
        source=source, email_valid=valid,
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
            company.last_verified_at, company.sources or [company.source], company.jd_promised_at,
        )  # fmt: skip
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO company (id, name, domain, source, industry, revenue_usd, employees,
                    country, locations, website, linkedin_url, career_page_url, is_client,
                    outreach_status, priority_score, hiring_history, last_verified_at, sources,
                    jd_promised_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name, domain = EXCLUDED.domain, industry = EXCLUDED.industry,
                    revenue_usd = EXCLUDED.revenue_usd, employees = EXCLUDED.employees,
                    country = EXCLUDED.country, locations = EXCLUDED.locations,
                    website = EXCLUDED.website, linkedin_url = EXCLUDED.linkedin_url,
                    career_page_url = EXCLUDED.career_page_url, is_client = EXCLUDED.is_client,
                    outreach_status = EXCLUDED.outreach_status,
                    priority_score = EXCLUDED.priority_score,
                    hiring_history = EXCLUDED.hiring_history,
                    last_verified_at = EXCLUDED.last_verified_at, sources = EXCLUDED.sources,
                    jd_promised_at = EXCLUDED.jd_promised_at, updated_at = now()
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
                    phone, linkedin_url, country, consent_basis, source, email_valid)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET full_name = EXCLUDED.full_name,
                    title = EXCLUDED.title, role_category = EXCLUDED.role_category,
                    email = EXCLUDED.email, phone = EXCLUDED.phone,
                    linkedin_url = EXCLUDED.linkedin_url, country = EXCLUDED.country,
                    consent_basis = EXCLUDED.consent_basis, email_valid = EXCLUDED.email_valid
                """,
                (
                    contact.id, contact.company_id, contact.full_name, contact.title,
                    contact.role.value, contact.email, contact.phone, contact.linkedin_url,
                    contact.country, contact.consent_basis.value, contact.source,
                    contact.email_valid,
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
    visa_status, work_authorization, expected_rate, resume_text, consent_status, education,
    industries, work_history, communication, resume_file"""


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
        values[19] = list(values[19] or [])
        values[20] = list(values[20] or [])
        values[21] = dict(values[21] or {})
        values[22] = dict(values[22]) if values[22] else None
        return Candidate(*values)

    def save(self, candidate: Candidate) -> Candidate:
        c = candidate
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO candidate (id, full_name, source, email, phone, linkedin_url,
                    current_location, preferred_location, skills, projects, experience_years,
                    availability, notice_period, visa_status, work_authorization, expected_rate,
                    resume_text, consent_status, education, industries, work_history, communication,
                    resume_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET full_name = EXCLUDED.full_name,
                    email = EXCLUDED.email, phone = EXCLUDED.phone,
                    linkedin_url = EXCLUDED.linkedin_url,
                    current_location = EXCLUDED.current_location,
                    preferred_location = EXCLUDED.preferred_location, skills = EXCLUDED.skills,
                    projects = EXCLUDED.projects, experience_years = EXCLUDED.experience_years,
                    availability = EXCLUDED.availability, notice_period = EXCLUDED.notice_period,
                    visa_status = EXCLUDED.visa_status,
                    work_authorization = EXCLUDED.work_authorization,
                    expected_rate = EXCLUDED.expected_rate, resume_text = EXCLUDED.resume_text,
                    consent_status = EXCLUDED.consent_status, education = EXCLUDED.education,
                    industries = EXCLUDED.industries, work_history = EXCLUDED.work_history,
                    communication = EXCLUDED.communication, resume_file = EXCLUDED.resume_file,
                    updated_at = now()
                """,
                (
                    c.id, c.full_name, c.source, c.email, c.phone, c.linkedin_url,
                    c.current_location, c.preferred_location, c.skills, Jsonb(c.projects),
                    c.experience_years, c.availability, c.notice_period, c.visa_status,
                    c.work_authorization, c.expected_rate, c.resume_text, c.consent_status.value,
                    c.education, c.industries, Jsonb(c.work_history), Jsonb(c.communication),
                    Jsonb(c.resume_file) if c.resume_file else None,
                ),
            )  # fmt: skip
        return candidate

    def add(self, candidate: Candidate) -> Candidate:
        return self.save(candidate)

    def find_duplicate(
        self, email: str | None, phone_digits: str | None, linkedin_slug: str | None
    ) -> Candidate | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT {_CANDIDATE} FROM candidate
                WHERE (%(email)s::text IS NOT NULL AND lower(email) = lower(%(email)s))
                   OR (%(phone)s::text IS NOT NULL
                       AND right(regexp_replace(coalesce(phone, ''), '[^0-9]', '', 'g'), 10)
                           = %(phone)s)
                   OR (%(slug)s::text IS NOT NULL
                       AND lower(rtrim(split_part(linkedin_url, 'linkedin.com/in/', 2), '/'))
                           = %(slug)s)
                ORDER BY created_at LIMIT 1
                """,
                {"email": email, "phone": phone_digits, "slug": linkedin_slug},
            ).fetchone()
        return self._candidate(row) if row else None

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


class PostgresRankings:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def save(self, ranking: Ranking) -> Ranking:
        ranking = carry_over_reviews(self.get(ranking.job_id), ranking)
        with self._connect() as conn:
            conn.execute("DELETE FROM candidate_ranking WHERE job_id = %s", (ranking.job_id,))
            conn.execute(
                "INSERT INTO candidate_ranking (job_id, ranked_at, weights, searched, widened)"
                " VALUES (%s, %s, %s, %s, %s)",
                (
                    ranking.job_id,
                    ranking.ranked_at,
                    Jsonb(ranking.weights),
                    ranking.searched,
                    Jsonb(ranking.widened) if ranking.widened is not None else None,
                ),  # fmt: skip
            )
            for e in ranking.candidates:
                conn.execute(
                    """
                    INSERT INTO candidate_ranking_entry (job_id, candidate_id, score, components,
                        strengths, gaps, rank, excluded_reason, verdict, note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ranking.job_id, e.candidate_id, e.score, Jsonb(e.components),
                        Jsonb(e.strengths), Jsonb(e.gaps), e.rank, e.excluded_reason,
                        e.verdict.value if e.verdict else None, e.note,
                    ),
                )  # fmt: skip
        return ranking

    def get(self, job_id: str) -> Ranking | None:
        if not is_uuid(job_id):
            return None
        with self._connect() as conn:
            head = conn.execute(
                "SELECT ranked_at, weights, searched, widened FROM candidate_ranking"
                " WHERE job_id = %s",
                (job_id,),
            ).fetchone()
            if head is None:
                return None
            rows = conn.execute(
                """
                SELECT candidate_id::text, score, components, strengths, gaps, rank,
                       excluded_reason, verdict, note
                FROM candidate_ranking_entry WHERE job_id = %s
                ORDER BY rank NULLS LAST, score DESC
                """,
                (job_id,),
            ).fetchall()
        entries = [
            RankedCandidate(cid, score, dict(comp), list(st), list(gp), rank, excl,
                            Verdict(verdict) if verdict else None, note)
            for cid, score, comp, st, gp, rank, excl, verdict, note in rows
        ]  # fmt: skip
        widened = dict(head[3]) if head[3] is not None else None
        return Ranking(job_id, head[0], dict(head[1]), entries, head[2], widened)

    def review(
        self, job_id: str, candidate_id: str, verdict: Verdict, note: str | None, by: str
    ) -> RankedCandidate:
        if not (is_uuid(job_id) and is_uuid(candidate_id)):
            raise UnknownRankingEntry(candidate_id)
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE candidate_ranking_entry
                SET verdict = %s, note = %s, reviewed_by = %s, reviewed_at = now()
                WHERE job_id = %s AND candidate_id = %s
                """,
                (verdict.value, note, by, job_id, candidate_id),
            ).rowcount
            if updated:
                conn.execute(
                    "INSERT INTO audit_log (actor, action, entity_type, entity_id, details)"
                    " VALUES (%s, 'ranking.review', 'job', %s, %s)",
                    (by, job_id, Jsonb({"candidate_id": candidate_id, "verdict": verdict.value})),
                )
        if not updated:
            raise UnknownRankingEntry(candidate_id)
        ranking = self.get(job_id)
        return next(c for c in ranking.candidates if c.candidate_id == candidate_id)

    def move(self, job_id: str, candidate_id: str, new_rank: int) -> Ranking:
        ranking = self.get(job_id)
        if ranking is None:
            raise UnknownRankingEntry(job_id)
        ranking = apply_move(ranking, candidate_id, new_rank)
        with self._connect() as conn:
            for e in ranking.candidates:
                conn.execute(
                    "UPDATE candidate_ranking_entry SET rank = %s"
                    " WHERE job_id = %s AND candidate_id = %s",
                    (e.rank, job_id, e.candidate_id),
                )
        return ranking


class PostgresWeights:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def get(self) -> RankingWeights:
        with self._connect() as conn:
            row = conn.execute("SELECT weights FROM ranking_settings WHERE id").fetchone()
        return RankingWeights(**row[0]) if row else RankingWeights()

    def save(self, weights: RankingWeights, by: str) -> RankingWeights:
        weights.validate()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ranking_settings (id, weights, updated_by) VALUES (true, %s, %s)
                ON CONFLICT (id) DO UPDATE SET weights = EXCLUDED.weights,
                    updated_by = EXCLUDED.updated_by, updated_at = now()
                """,
                (Jsonb(weights_dict(weights)), by),
            )
            conn.execute(
                "INSERT INTO audit_log (actor, action, entity_type, entity_id, details)"
                " VALUES (%s, 'ranking_weights.update', 'ranking_settings', 'default', %s)",
                (by, Jsonb(weights_dict(weights))),
            )
        return weights


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


_ENGAGEMENT = """id::text, job_id::text, candidate_id::text, status, approvals, token_hash,
    token_expires_at, requested_at, consent_store_at, consent_submit_at, consent_evidence, missing,
    next_reminder_at, history, updated_at"""


class PostgresEngagements:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    @staticmethod
    def _row(row: tuple) -> Engagement:
        values = list(row)
        values[3] = EngagementStatus(values[3])
        values[4] = dict(values[4] or {})
        values[5] = values[5].strip() if values[5] else None
        values[11] = list(values[11] or [])
        values[13] = list(values[13] or [])
        return Engagement(*values)

    def _query(self, where: str, params: tuple) -> list[Engagement]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_ENGAGEMENT} FROM candidate_engagement WHERE {where}"
                " ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, engagement_id: str) -> Engagement | None:
        if not is_uuid(engagement_id):
            return None
        return next(iter(self._query("id = %s", (engagement_id,))), None)

    def find(self, job_id: str, candidate_id: str) -> Engagement | None:
        if not (is_uuid(job_id) and is_uuid(candidate_id)):
            return None
        return next(
            iter(self._query("job_id = %s AND candidate_id = %s", (job_id, candidate_id))), None
        )

    def by_token_hash(self, token_hash: str) -> Engagement | None:
        return next(iter(self._query("token_hash = %s", (token_hash,))), None)

    def by_approval(self, approval_request_id: str) -> Engagement | None:
        rows = self._query(
            "EXISTS (SELECT 1 FROM jsonb_each_text(approvals) a WHERE a.value = %s)",
            (approval_request_id,),
        )
        return next(iter(rows), None)

    def for_candidate(self, candidate_id: str) -> list[Engagement]:
        return self._query("candidate_id = %s", (candidate_id,)) if is_uuid(candidate_id) else []

    def list(
        self, job_id: str | None = None, status: EngagementStatus | None = None
    ) -> list[Engagement]:
        if job_id is not None and not is_uuid(job_id):
            return []
        return self._query(
            "(%s::uuid IS NULL OR job_id = %s::uuid) AND (%s::text IS NULL OR status = %s)",
            (job_id, job_id, status.value if status else None, status.value if status else None),
        )

    def save(self, engagement: Engagement) -> Engagement:
        e = engagement
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO candidate_engagement (id, job_id, candidate_id, status, approvals,
                    token_hash, token_expires_at, requested_at, consent_store_at,
                    consent_submit_at, consent_evidence, missing, next_reminder_at, history,
                    updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s, now()))
                ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status,
                    approvals = EXCLUDED.approvals, token_hash = EXCLUDED.token_hash,
                    token_expires_at = EXCLUDED.token_expires_at,
                    requested_at = EXCLUDED.requested_at,
                    consent_store_at = EXCLUDED.consent_store_at,
                    consent_submit_at = EXCLUDED.consent_submit_at,
                    consent_evidence = EXCLUDED.consent_evidence, missing = EXCLUDED.missing,
                    next_reminder_at = EXCLUDED.next_reminder_at, history = EXCLUDED.history,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    e.id, e.job_id, e.candidate_id, e.status.value, Jsonb(e.approvals),
                    e.token_hash, e.token_expires_at, e.requested_at, e.consent_store_at,
                    e.consent_submit_at, e.consent_evidence, e.missing, e.next_reminder_at,
                    Jsonb(e.history), e.updated_at,
                ),
            )  # fmt: skip
        return engagement


_SUBMISSION = """id::text, job_id::text, candidate_id::text, client_company_id::text, status, match,
    package, recipient, approval_request_id::text, right_to_represent_at, right_to_represent_by,
    warnings, rejection_reason, sent_at, sent_content, follow_up_at, client_decision,
    client_feedback, history, created_at, updated_at, stage, stage_at, interview_at, offer_at,
    start_date, stage_reason"""


class PostgresSubmissions:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    @staticmethod
    def _row(row: tuple) -> Submission:
        values = list(row)
        values[4] = SubmissionStatus(values[4])
        values[11] = list(values[11] or [])
        values[16] = ClientDecision(values[16]) if values[16] else None
        values[21] = PlacementStage(values[21]) if values[21] else None
        return Submission(*values)

    def _query(self, where: str, params: tuple) -> list[Submission]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_SUBMISSION} FROM client_submission WHERE {where}"
                " ORDER BY created_at DESC",
                params,
            ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, submission_id: str) -> Submission | None:
        if not is_uuid(submission_id):
            return None
        return next(iter(self._query("id = %s", (submission_id,))), None)

    def by_approval(self, approval_request_id: str) -> Submission | None:
        if not is_uuid(approval_request_id):
            return None
        return next(iter(self._query("approval_request_id = %s", (approval_request_id,))), None)

    def list(
        self,
        job_id: str | None = None,
        candidate_id: str | None = None,
        client_company_id: str | None = None,
        status: SubmissionStatus | None = None,
    ) -> list[Submission]:
        ids = (job_id, candidate_id, client_company_id)
        if any(i is not None and not is_uuid(i) for i in ids):
            return []
        value = status.value if status else None
        return self._query(
            "(%s::uuid IS NULL OR job_id = %s::uuid)"
            " AND (%s::uuid IS NULL OR candidate_id = %s::uuid)"
            " AND (%s::uuid IS NULL OR client_company_id = %s::uuid)"
            " AND (%s::text IS NULL OR status = %s)",
            (job_id, job_id, candidate_id, candidate_id, client_company_id, client_company_id,
             value, value),
        )  # fmt: skip

    def save(self, submission: Submission) -> Submission:
        s = submission
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO client_submission (id, job_id, candidate_id, client_company_id,
                    status, match, package, recipient, approval_request_id,
                    right_to_represent_at, right_to_represent_by, warnings, rejection_reason,
                    sent_at, sent_content, follow_up_at, client_decision, client_feedback,
                    history, created_at, updated_at, stage, stage_at, interview_at,
                    offer_at, start_date, stage_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, coalesce(%s, now()), coalesce(%s, now()), %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status,
                    match = EXCLUDED.match, package = EXCLUDED.package,
                    recipient = EXCLUDED.recipient,
                    approval_request_id = EXCLUDED.approval_request_id,
                    right_to_represent_at = EXCLUDED.right_to_represent_at,
                    right_to_represent_by = EXCLUDED.right_to_represent_by,
                    warnings = EXCLUDED.warnings, rejection_reason = EXCLUDED.rejection_reason,
                    sent_at = EXCLUDED.sent_at, sent_content = EXCLUDED.sent_content,
                    follow_up_at = EXCLUDED.follow_up_at,
                    client_decision = EXCLUDED.client_decision,
                    client_feedback = EXCLUDED.client_feedback, history = EXCLUDED.history,
                    updated_at = EXCLUDED.updated_at, stage = EXCLUDED.stage,
                    stage_at = EXCLUDED.stage_at, interview_at = EXCLUDED.interview_at,
                    offer_at = EXCLUDED.offer_at, start_date = EXCLUDED.start_date,
                    stage_reason = EXCLUDED.stage_reason
                """,
                (
                    s.id, s.job_id, s.candidate_id, s.client_company_id, s.status.value,
                    Jsonb(s.match), Jsonb(s.package), s.recipient, s.approval_request_id,
                    s.right_to_represent_at, s.right_to_represent_by, s.warnings,
                    s.rejection_reason, s.sent_at, Jsonb(s.sent_content), s.follow_up_at,
                    s.client_decision.value if s.client_decision else None, s.client_feedback,
                    Jsonb(s.history), s.created_at, s.updated_at,
                    s.stage.value if s.stage else None, s.stage_at, s.interview_at,
                    s.offer_at, s.start_date, s.stage_reason,
                ),
            )  # fmt: skip
        return submission


_FOLLOW_UP = """id::text, kind, subject_id, owner_role, anchor_at, due_at, status, attempt,
    job_id::text, candidate_id::text, company_id::text, owner_id, note, approvals, context,
    history, created_at, updated_at"""


class PostgresFollowUps:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    @staticmethod
    def _row(row: tuple) -> FollowUp:
        values = list(row)
        values[1] = FollowUpKind(values[1])
        values[6] = FollowUpStatus(values[6])
        values[13] = list(values[13] or [])
        return FollowUp(*values)

    def _query(self, where: str, params: tuple) -> list[FollowUp]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_FOLLOW_UP} FROM follow_up WHERE {where}"
                " ORDER BY due_at NULLS LAST, created_at",
                params,
            ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, follow_up_id: str) -> FollowUp | None:
        if not is_uuid(follow_up_id):
            return None
        return next(iter(self._query("id = %s", (follow_up_id,))), None)

    def find(self, kind: FollowUpKind, subject_id: str) -> FollowUp | None:
        rows = self._query("kind = %s AND subject_id = %s", (kind.value, subject_id))
        return next(iter(rows), None)

    def list(
        self, status: FollowUpStatus | None = None, job_id: str | None = None
    ) -> list[FollowUp]:
        if job_id is not None and not is_uuid(job_id):
            return []
        value = status.value if status else None
        return self._query(
            "(%s::text IS NULL OR status = %s) AND (%s::uuid IS NULL OR job_id = %s::uuid)",
            (value, value, job_id, job_id),
        )

    def save(self, item: FollowUp) -> FollowUp:
        f = item
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO follow_up (id, kind, subject_id, owner_role, anchor_at, due_at,
                    status, attempt, job_id, candidate_id, company_id, owner_id, note, approvals,
                    context, history, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    coalesce(%s, now()), coalesce(%s, now()))
                ON CONFLICT (id) DO UPDATE SET owner_role = EXCLUDED.owner_role,
                    anchor_at = EXCLUDED.anchor_at, due_at = EXCLUDED.due_at,
                    status = EXCLUDED.status, attempt = EXCLUDED.attempt,
                    owner_id = EXCLUDED.owner_id, note = EXCLUDED.note,
                    approvals = EXCLUDED.approvals, context = EXCLUDED.context,
                    history = EXCLUDED.history, updated_at = EXCLUDED.updated_at
                """,
                (
                    f.id, f.kind.value, f.subject_id, f.owner_role, f.anchor_at, f.due_at,
                    f.status.value, f.attempt, f.job_id, f.candidate_id, f.company_id,
                    f.owner_id, f.note, f.approvals, Jsonb(f.context), Jsonb(f.history),
                    f.created_at, f.updated_at,
                ),
            )  # fmt: skip
        return item


class PostgresCadence:
    """Q13 cadence; kinds without a saved row use the default."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def get(self) -> dict[FollowUpKind, list[int]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT kind, days FROM follow_up_cadence").fetchall()
        saved = {FollowUpKind(k): list(d) for k, d in rows if k in FollowUpKind._value2member_map_}
        return {k: saved.get(k, list(v)) for k, v in DEFAULT_CADENCE.items()}

    def save(self, kind: FollowUpKind, days: list[int], by: str) -> None:
        days = validate_cadence(days)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO follow_up_cadence (kind, days, updated_by) VALUES (%s, %s, %s)
                ON CONFLICT (kind) DO UPDATE SET days = EXCLUDED.days,
                    updated_by = EXCLUDED.updated_by, updated_at = now()
                """,
                (kind.value, days, by),
            )
