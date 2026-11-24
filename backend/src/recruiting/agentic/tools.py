"""Tools the deep agents act through. Each wraps the platform's deterministic code.

Guardrails live here, not only in prompts:
- No tool sends anything. Messages are drafted into the approval gates (BRD section 10) and
  go out only after a person approves them.
- Human-only decisions have no tool: approving or rejecting a draft, recruiter verdicts on
  candidates, the right-to-represent confirmation, consent, placement stages, client feedback.
- Opt-out, CASL, bounce, duplicate-submission and consent checks run inside the wrapped code,
  so an agent cannot skip them.
- Tools are bound to one subject (a job, an email) where the agent works on one subject, so a
  run cannot touch another job's candidates.
Every tool returns compact JSON and records what it did on the run (ToolContext.actions).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.tools import BaseTool, tool

from recruiting.agents.candidate_search import Widening, qualified_count, shortfall
from recruiting.agents.jd_intake import InboundEmail
from recruiting.ai.prompts import ExtractedJob, JDExtraction, ReplyClassification
from recruiting.records.models import OutreachStatus, Verdict

if TYPE_CHECKING:
    from recruiting.api.platform import Platform

MAX_OUTPUT = 20_000


@dataclass
class ToolContext:
    platform: Platform
    agent: str
    recruiter: str = "CBS recruiting team"  # name used in drafted messages
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    actions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def actor(self) -> str:
        return f"agent:{self.agent}"

    def act(self, tool_name: str, **details: Any) -> None:
        self.actions.append({"tool": tool_name, "at": self.clock().isoformat(), **details})


def out(value: Any) -> str:
    text = json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + '..."(truncated)"'


def error(message: str) -> str:
    return out({"error": message})


# ----- recruiting coordinator (Agents 6-11), bound to one job ----------


def recruiting_tools(ctx: ToolContext, job_id: str) -> dict[str, BaseTool]:
    p = ctx.platform

    def job_or_none():
        return p.jobs.get(job_id)

    def ranked_rows(ranking, top: int) -> list[dict[str, Any]]:
        rows = []
        for entry in ranking.top[:top]:
            candidate = p.candidates.get(entry.candidate_id)
            rows.append(
                {
                    "candidate_id": entry.candidate_id,
                    "name": candidate.full_name if candidate else None,
                    "rank": entry.rank,
                    "score": entry.score,
                    "verdict": entry.verdict.value if entry.verdict else None,
                    "strengths": entry.strengths[:3],
                    "gaps": entry.gaps[:3],
                    "has_email": bool(candidate and candidate.email),
                    "has_linkedin": bool(candidate and candidate.linkedin_url),
                }
            )
        return rows

    @tool
    def get_job() -> str:
        """The job this run works on: title, status, location, must-have and nice-to-have
        skills, fields the Account Manager still has to confirm, and the recruiter."""
        job = job_or_none()
        if job is None:
            return error("The job no longer exists")
        analysis = job.analysis or {}
        return out(
            {
                "job_id": job.id,
                "job_code": job.job_code,
                "title": job.title,
                "status": job.status.value,
                "priority": job.priority.value,
                "location": job.location,
                "experience": job.experience,
                "visa_requirements": job.visa_requirements,
                "work_mode": job.work_mode,
                "must_have_skills": analysis.get("must_have_skills") or job.required_skills,
                "nice_to_have_skills": analysis.get("nice_to_have_skills") or job.preferred_skills,
                "search_summary": analysis.get("search_summary"),
                "fields_to_confirm": job.review_fields,
                "recruiter_id": job.recruiter_id,
            }
        )

    @tool
    def search_and_rank(limit: int = 20) -> str:
        """Search the Candidate Database and licensed sources for this job (Agent 6) and rank
        the candidates 0-100 (Agent 7). Replaces the job's ranking; recruiter verdicts are kept.
        Returns the ranked candidates and whether the job is short of qualified candidates."""
        job = job_or_none()
        if job is None:
            return error("The job no longer exists")
        ranking, report = p.candidate_search.run(job, limit=max(1, min(limit, 50)))
        ranking = p.rankings.save(ranking)
        ctx.act("search_and_rank", job_id=job_id, ranked=len(ranking.top), pool=report.pool)
        return out(
            {
                "searched": report.pool,
                "source_errors": report.external_errors,
                "ranked": ranked_rows(ranking, 20),
                "shortfall": shortfall(job, ranking),
            }
        )

    @tool
    def get_ranking(top: int = 20) -> str:
        """The job's current ranking (best first) with recruiter verdicts, and the shortfall."""
        job = job_or_none()
        ranking = p.rankings.get(job_id)
        if job is None or ranking is None:
            return error("No ranking yet: call search_and_rank first")
        return out(
            {
                "ranked": ranked_rows(ranking, max(1, min(top, 50))),
                "qualified": qualified_count(ranking),
                "widened": ranking.widened,
                "shortfall": shortfall(job, ranking),
            }
        )

    @tool
    def widen_search(
        title_variants: list[str] | None = None,
        location: Literal["state", "anywhere"] | None = None,
        include_nice_to_have: bool = False,
    ) -> str:
        """Scenario S12: search again more widely when fewer than 10 candidates qualify. Use
        the shortfall suggestions: related job titles, the whole state or anywhere for the
        external sources, nice-to-have skills in the skill filter. The recruiter approves this
        call before it runs."""
        job = job_or_none()
        if job is None:
            return error("The job no longer exists")
        variants = tuple(dict.fromkeys(t.strip()[:120] for t in title_variants or [] if t.strip()))
        widen = Widening(variants[:10], location, include_nice_to_have)
        if widen == Widening():
            return error("Choose at least one way to widen the search")
        before = p.rankings.get(job_id)
        ranking, report = p.candidate_search.run(job, limit=20, widen=widen)
        ranking.widened = {
            **widen.as_dict(),
            "by": ctx.actor,
            "at": ctx.clock().isoformat(),
            "before": len(before.top) if before else None,
        }
        ranking = p.rankings.save(ranking)
        ctx.act("widen_search", job_id=job_id, **widen.as_dict(), ranked=len(ranking.top))
        return out(
            {
                "ranked": ranked_rows(ranking, 20),
                "qualified": qualified_count(ranking),
                "shortfall": shortfall(job, ranking),
            }
        )

    @tool
    def list_engagements() -> str:
        """Where each contacted candidate is for this job: drafted, contacted, interested,
        details requested, complete, submitted, declined or do-not-contact; missing details."""
        rows = []
        for e in p.engagements.list(job_id=job_id):
            candidate = p.candidates.get(e.candidate_id)
            if p.candidate_outreach is not None:
                e = p.candidate_outreach.refresh(e)
            rows.append(
                {
                    "engagement_id": e.id,
                    "candidate_id": e.candidate_id,
                    "name": candidate.full_name if candidate else None,
                    "status": e.status.value,
                    "missing": e.missing,
                    "consent_for_this_job": e.consent_submit_at is not None,
                }
            )
        return out({"engagements": rows})

    @tool
    def draft_outreach(candidate_ids: list[str], channels: list[str] | None = None) -> str:
        """Agent 8: draft first messages (BRD templates) to ranked candidates. channels:
        "email" and/or "linkedin". Drafts wait for the recruiter's approval; nothing is sent
        now. Only candidates in this job's ranking who were not marked not-qualified can be
        contacted; opted-out and already-contacted people are skipped automatically."""
        job, ranking = job_or_none(), p.rankings.get(job_id)
        if job is None or ranking is None:
            return error("No ranking yet: call search_and_rank first")
        if p.candidate_outreach is None:
            return error("Outreach workflows are not running")
        allowed = {c.candidate_id for c in ranking.top if c.verdict is not Verdict.NOT_QUALIFIED}
        chosen = list(dict.fromkeys(candidate_ids))[:20]
        not_ranked = [c for c in chosen if c not in allowed]
        wanted = [c for c in channels or ["email"] if c in ("email", "linkedin")] or ["email"]
        report = p.candidate_outreach.draft(
            job, [c for c in chosen if c in allowed], wanted, ctx.recruiter
        )
        if not_ranked:
            report.skipped.setdefault("not_in_ranking", []).extend(not_ranked)
        ctx.act(
            "draft_outreach",
            job_id=job_id,
            drafted=len(report.drafted),
            skipped={k: len(v) for k, v in report.skipped.items()},
        )
        return out({"drafted_for_approval": report.drafted, "skipped": report.skipped})

    @tool
    def request_candidate_details(candidate_id: str) -> str:
        """Agent 9: ask an interested candidate for their resume, the seven details and
        consent, through a secure link. The request email waits for the recruiter's approval."""
        engagement = p.engagements.find(job_id, candidate_id)
        if engagement is None:
            return error("This candidate has not been contacted for this job")
        try:
            engagement, _link = p.resume_collection.request(engagement.id, ctx.recruiter)
        except Exception as exc:  # CollectionError and friends: tell the agent why
            return error(str(exc))
        ctx.act("request_candidate_details", job_id=job_id, candidate_id=candidate_id)
        return out(
            {
                "status": engagement.status.value,
                "missing": engagement.missing,
                "approval_request_id": engagement.approvals.get("details_request"),
            }
        )

    @tool
    def match_candidate(candidate_id: str) -> str:
        """Agent 10: match report for a candidate against this job (score, strengths,
        concerns, missing skills, flags, interview probability) and a draft client package.
        The recruiter reviews it and sends it for approval; this does not submit anyone."""
        try:
            submission = p.submissions.prepare(job_id, candidate_id, ctx.actor)
        except Exception as exc:  # duplicate submission, closed job, unknown candidate
            return error(str(exc))
        m = submission.match
        ctx.act(
            "match_candidate",
            job_id=job_id,
            candidate_id=candidate_id,
            submission_id=submission.id,
            score=m.get("score"),
        )
        return out(
            {
                "submission_id": submission.id,
                "score": m.get("score"),
                "strengths": m.get("strengths"),
                "concerns": m.get("concerns"),
                "missing_skills": m.get("missing_skills"),
                "flags": m.get("flags"),
                "interview_probability": m.get("interview_probability"),
                "notes": m.get("notes"),
            }
        )

    @tool
    def update_submission(
        submission_id: str, candidate_summary: str | None = None, notes: str | None = None
    ) -> str:
        """Improve the draft package before the recruiter sees it: the candidate summary for
        the client and the recruiter's notes. Keep to facts from the resume and match report."""
        changes = {
            k: v[:2000]
            for k, v in (("candidate_summary", candidate_summary), ("notes", notes))
            if v
        }
        if not changes:
            return error("Nothing to change")
        try:
            submission = p.submissions.get(submission_id)
            if submission.job_id != job_id:
                return error("That submission belongs to another job")
            submission = p.submissions.edit(submission_id, changes, ctx.actor)
        except Exception as exc:
            return error(str(exc))
        ctx.act("update_submission", submission_id=submission_id, fields=sorted(changes))
        return out({"submission_id": submission.id, "status": submission.status.value})

    @tool
    def list_submissions() -> str:
        """Submissions for this job: draft (matched), waiting for approval, sent, rejected."""
        rows = [
            {
                "submission_id": s.id,
                "candidate_id": s.candidate_id,
                "status": s.status.value,
                "score": s.match.get("score"),
                "stage": s.stage.value if s.stage else None,
            }
            for s in p.submissions.list(job_id=job_id)
        ]
        return out({"submissions": rows})

    return {
        t.name: t
        for t in (
            get_job,
            search_and_rank,
            get_ranking,
            widen_search,
            list_engagements,
            draft_outreach,
            request_candidate_details,
            match_candidate,
            update_submission,
            list_submissions,
        )
    }


# ----- business development (Agents 1-3) ----------


def business_development_tools(ctx: ToolContext) -> dict[str, BaseTool]:
    p = ctx.platform

    @tool
    def run_company_discovery() -> str:
        """Agent 1: pull companies from the active company-data sources, keep those that meet
        the criteria (USD 100M+ revenue, USA or Canada, target industries), merge duplicates."""
        report = p.discovery.run()
        ctx.act("run_company_discovery", created=report.created, merged=report.merged)
        return out(
            {
                "seen": report.seen,
                "created": report.created,
                "updated": report.updated,
                "merged": report.merged,
                "skipped": report.skipped,
                "source_errors": report.source_errors,
            }
        )

    @tool
    def check_hiring_signals(company_ids: list[str] | None = None) -> str:
        """Agent 2: read career pages for open engineering roles and refresh each company's
        0-100 priority score. Without ids, checks the companies that are due."""
        if company_ids:
            companies = [c for cid in company_ids[:50] if (c := p.companies.get(cid))]
            report = p.signals.run(companies)
        else:
            report = p.signals.run_due()
        ctx.act("check_hiring_signals", checked=report.companies_checked)
        return out(
            {
                "checked": report.companies_checked,
                "new_postings": report.new_postings,
                "scores": report.scores,
                "pages_unavailable": report.pages_unavailable,
            }
        )

    @tool
    def list_prospects(min_priority: int = 1, limit: int = 20) -> str:
        """Companies not contacted yet, highest priority first, with their open target roles."""
        rows = []
        for c in p.companies.list(min_priority=max(0, min_priority), limit=500):
            if c.outreach_status is not OutreachStatus.NOT_CONTACTED:
                continue
            roles = sorted({x.target_role for x in p.companies.postings(c.id) if x.target_role})
            rows.append(
                {
                    "company_id": c.id,
                    "name": c.name,
                    "industry": c.industry,
                    "country": c.country,
                    "priority": c.priority_score,
                    "roles": roles,
                }
            )
            if len(rows) >= max(1, min(limit, 50)):
                break
        return out({"prospects": rows})

    @tool
    def get_company(company_id: str) -> str:
        """A company's profile, open target roles, and its contacts with whether each may be
        emailed now (and why not: opted out, bounced, no CASL consent, no email)."""
        company = p.companies.get(company_id)
        if company is None:
            return error("Unknown company")
        agent = p.client_outreach
        contacts = [
            {
                "contact_id": c.id,
                "name": c.full_name,
                "title": c.title,
                "role": c.role.value,
                "may_email": agent is not None and agent.contact_problem(company, c) is None,
                "problem": agent.contact_problem(company, c) if agent else None,
            }
            for c in p.companies.contacts(company.id)
        ]
        postings = [
            {
                "title": x.title,
                "role": x.target_role,
                "location": x.location,
                "posted": x.date_posted,
            }
            for x in p.companies.postings(company.id)
        ][:15]
        return out(
            {
                "company_id": company.id,
                "name": company.name,
                "industry": company.industry,
                "revenue_usd": company.revenue_usd,
                "country": company.country,
                "locations": company.locations[:5],
                "priority": company.priority_score,
                "outreach_status": company.outreach_status.value,
                "contacts": contacts,
                "open_roles": postings,
            }
        )

    @tool
    def draft_first_email(company_id: str, contact_id: str, subject: str, body: str) -> str:
        """Agent 3: put a personalised first email to one contact in the BD Manager's approval
        queue (nothing is sent now). Write a short, specific email about the company's open
        engineering roles and how CBS LLC can help; no claims you cannot support. The
        unsubscribe footer and postal address are added automatically."""
        agent = p.client_outreach
        company = p.companies.get(company_id)
        contact = next((c for c in p.companies.contacts(company_id) if c.id == contact_id), None)
        if agent is None:
            return error("Outreach workflows are not running")
        if company is None or contact is None:
            return error("Unknown company or contact (the contact must belong to the company)")
        if company.outreach_status is not OutreachStatus.NOT_CONTACTED:
            return error(f"The company is already {company.outreach_status.value}")
        if company.priority_score <= 0:
            return error("No current hiring signal: check_hiring_signals first")
        problem = agent.contact_problem(company, contact)
        if problem:
            return error(f"This contact may not be emailed: {problem}")
        if not (3 <= len(subject.strip()) <= 150 and 80 <= len(body.strip()) <= 3000):
            return error("Subject 3-150 characters and body 80-3000 characters, please")
        request_id = agent.start_draft(company, contact, subject, body, ctx.actor)
        ctx.act(
            "draft_first_email",
            company_id=company_id,
            contact_id=contact_id,
            approval_request_id=request_id,
        )
        return out({"approval_request_id": request_id, "status": "waiting_for_bd_manager"})

    return {
        t.name: t
        for t in (
            run_company_discovery,
            check_hiring_signals,
            list_prospects,
            get_company,
            draft_first_email,
        )
    }


# ----- intake (Agents 4-5), bound to one email ----------


def intake_tools(ctx: ToolContext, email: InboundEmail) -> dict[str, BaseTool]:
    p = ctx.platform
    filed: list[bool] = []

    @tool
    def lookup_sender() -> str:
        """Who sent this email: a known client contact (company, outreach status, open jobs)
        or a candidate in the Candidate Database, and the outreach email it answers."""
        contact = p.companies.contact_by_email(email.from_address)
        company = p.companies.get(contact.company_id) if contact else None
        candidate = p.candidates.find_duplicate(email.from_address.lower(), None, None)
        delivery = p.deliveries.latest_to(email.from_address)
        open_jobs = (
            [j.job_code for j in p.jobs.list(client_company_id=company.id)] if company else []
        )
        return out(
            {
                "contact": {"name": contact.full_name, "title": contact.title} if contact else None,
                "company": {
                    "name": company.name,
                    "outreach_status": company.outreach_status.value,
                    "is_client": company.is_client,
                    "jobs": open_jobs,
                }
                if company
                else None,
                "is_candidate": candidate is not None,
                "answers_email": delivery.message.content.get("subject") if delivery else None,
            }
        )

    @tool
    def file_client_reply(
        category: Literal[
            "jd_received",
            "interested",
            "jd_to_follow",
            "not_now",
            "not_interested",
            "unsubscribe",
            "out_of_office",
            "other",
        ],
        summary: str,
        jobs: list[ExtractedJob] | None = None,
    ) -> str:
        """Record what this client email is and act on it (call once per email). For
        jd_received, give one entry in `jobs` per distinct position with the facts copied
        from the email and attachments (null when not stated) and the guessed fields in
        low_confidence_fields; the Account Manager confirms those. unsubscribe stops all
        outreach to the sender; jd_to_follow sets a reminder; out_of_office changes nothing."""  # noqa: E501
        if filed:
            return error("This email was already filed")
        if category == "jd_received" and not jobs:
            return error("Give the extracted jobs for jd_received")
        classification = ReplyClassification(category=category, summary=summary[:500])
        extraction = JDExtraction(jobs=jobs) if jobs else None
        result = p.jd_intake.apply(email, classification, "agent", extraction)
        filed.append(True)
        ctx.act("file_client_reply", category=category, job_ids=result.job_ids)
        return out(
            {
                "category": result.category,
                "company_id": result.company_id,
                "job_ids": result.job_ids,
                "note": None
                if result.company_id or category != "jd_received"
                else "Unknown sender: the JD waits for a person to link it to a company",
            }
        )

    @tool
    def handle_candidate_reply() -> str:
        """For a reply from a candidate: Agent 9 records interest, details, written consent or
        an unsubscribe from the email and attachments (call once per email)."""
        if filed:
            return error("This email was already filed")
        candidate = p.candidates.find_duplicate(email.from_address.lower(), None, None)
        if candidate is None:
            return error("The sender is not a known candidate")
        result = p.resume_collection.process_reply(email, candidate)
        filed.append(True)
        ctx.act("handle_candidate_reply", candidate_id=candidate.id, intent=result.intent)
        return out(
            {"candidate_id": candidate.id, "intent": result.intent, "updated": result.updated}
        )

    return {t.name: t for t in (lookup_sender, file_client_reply, handle_candidate_reply)}


# ----- follow-up (Agent 12) ----------


def follow_up_tools(ctx: ToolContext) -> dict[str, BaseTool]:
    p = ctx.platform

    @tool
    def run_follow_up_sweep() -> str:
        """Bring the follow-up list up to date with the records (creates, reschedules and
        closes items). Does not draft anything."""
        report = p.follow_ups.run(auto_draft=False)
        ctx.act("run_follow_up_sweep", created=report.created, closed=report.closed)
        return out(
            {"created": report.created, "closed": report.closed, "cancelled": report.cancelled}
        )

    @tool
    def list_due_follow_ups(limit: int = 30) -> str:
        """Open follow-ups that are due now: kind, what is waiting, owner, attempt number,
        whether a message can be drafted (has a recipient)."""
        now = ctx.clock()
        rows = []
        for i in p.follow_ups.for_user("administrator", None):
            if i.due_at and i.due_at <= now:
                rows.append(
                    {
                        "follow_up_id": i.id,
                        "kind": i.kind.value,
                        "note": i.note,
                        "owner_role": i.owner_role,
                        "attempt": i.attempt,
                        "due_at": i.due_at,
                        "can_draft_message": bool(i.context.get("recipient")),
                    }
                )
        rows.sort(key=lambda r: r["due_at"])
        return out({"due": rows[: max(1, min(limit, 100))]})

    @tool
    def draft_follow_up(follow_up_id: str) -> str:
        """Draft the next follow-up message for an item (into the same approval gate as the
        first contact; nothing is sent now)."""
        try:
            drafted = p.follow_ups.draft_now(follow_up_id, ctx.actor)
        except Exception as exc:  # not open, no recipient, opted out, already pending
            return error(str(exc))
        ctx.act("draft_follow_up", follow_up_id=follow_up_id)
        return out(drafted)

    @tool
    def snooze_follow_up(follow_up_id: str, days: int, reason: str) -> str:
        """Push an item's due date back by 1-14 days, with the reason (e.g. the client said
        they are travelling this week)."""
        days = max(1, min(days, 14))
        try:
            item = p.follow_ups.snooze(follow_up_id, ctx.clock() + timedelta(days=days), ctx.actor)
        except Exception as exc:
            return error(str(exc))
        ctx.act("snooze_follow_up", follow_up_id=follow_up_id, days=days, reason=reason[:200])
        return out({"follow_up_id": item.id, "due_at": item.due_at})

    return {
        t.name: t
        for t in (run_follow_up_sweep, list_due_follow_ups, draft_follow_up, snooze_follow_up)
    }
