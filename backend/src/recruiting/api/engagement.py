"""Candidate engagement API (Agents 8-9, plan section 9) and the candidate's secure link.

Staff endpoints need the Recruiter, Account Manager or Administrator role. The /collect pages
are public: the unguessable, expiring token in the link is the candidate's only credential.
"""

from __future__ import annotations

import html
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Form, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from recruiting.agents.resume_collection import LABELS, CollectedDetails, CollectionError
from recruiting.api.deps import ServicesDep, UserDep, require_platform, require_role
from recruiting.domain import Role
from recruiting.records.models import DETAIL_ITEMS, Engagement

router = APIRouter(tags=["engagement"])

TALENT = (Role.ADMINISTRATOR, Role.RECRUITER, Role.ACCOUNT_MANAGER)
SENDERS = (Role.ADMINISTRATOR, Role.RECRUITER)


class OutreachIn(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=50)
    channels: list[Literal["linkedin", "email"]] = Field(default=["linkedin"], min_length=1)


class OutcomeIn(BaseModel):
    outcome: Literal["accepted", "replied", "interested", "not_interested", "do_not_contact"]


def _outreach_agent(services):
    agent = require_platform(services).candidate_outreach
    if agent is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Outreach workflows are not running"
        )
    return agent


def _engagement_out(services, engagement: Engagement) -> dict:
    platform = services.platform
    candidate = platform.candidates.get(engagement.candidate_id)
    out = asdict(engagement)
    out.pop("token_hash", None)
    out["candidate_name"] = candidate.full_name if candidate else None
    out["consent_recorded"] = bool(engagement.consent_store_at and engagement.consent_submit_at)
    return out


@router.post("/jobs/{job_id}/outreach")
def draft_outreach(job_id: str, body: OutreachIn, user: UserDep, services: ServicesDep) -> dict:
    """Agent 8: each draft waits in the recruiter's approval queue (BRD section 10)."""
    require_role(user, SENDERS)
    platform = require_platform(services)
    job = platform.jobs.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    report = _outreach_agent(services).draft(
        job, body.candidate_ids, body.channels, user.display_name
    )
    return asdict(report)


@router.get("/jobs/{job_id}/engagements")
def list_engagements(job_id: str, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, TALENT)
    platform = require_platform(services)
    agent = platform.candidate_outreach
    items = platform.engagements.list(job_id=job_id)
    if agent is not None:
        items = [agent.refresh(e) for e in items]
    return {
        "summary": platform.resume_collection.summary(job_id),
        "engagements": [_engagement_out(services, e) for e in items],
    }


@router.post("/engagements/{engagement_id}/outcome")
def record_outcome(
    engagement_id: str, body: OutcomeIn, user: UserDep, services: ServicesDep
) -> dict:
    """Recruiter records what happened; "interested" starts Agent 9's request."""
    require_role(user, SENDERS)
    platform = require_platform(services)
    try:
        engagement = _outreach_agent(services).record_outcome(
            engagement_id, body.outcome, user.user_id
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Engagement not found") from exc
    link = None
    if body.outcome == "interested":
        engagement, link = platform.resume_collection.request(engagement.id, user.display_name)
    return {**_engagement_out(services, engagement), "upload_link": link}


@router.post("/engagements/{engagement_id}/request-details")
def request_details(engagement_id: str, user: UserDep, services: ServicesDep) -> dict:
    """Agent 9: drafts the request email (approval gate) and returns a fresh secure link."""
    require_role(user, SENDERS)
    try:
        engagement, link = require_platform(services).resume_collection.request(
            engagement_id, user.display_name
        )
    except CollectionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {**_engagement_out(services, engagement), "upload_link": link}


@router.get("/linkedin-tasks")
def linkedin_tasks(user: UserDep, services: ServicesDep) -> list[dict]:
    """Q15: approved connection notes for the recruiter to send by hand."""
    require_role(user, SENDERS)
    return _outreach_agent(services).linkedin_tasks()


@router.post("/linkedin-tasks/{approval_request_id}/sent")
def linkedin_sent(approval_request_id: str, user: UserDep, services: ServicesDep) -> dict:
    require_role(user, SENDERS)
    try:
        engagement = _outreach_agent(services).mark_linkedin_sent(approval_request_id)
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No approved LinkedIn note with this id"
        ) from exc
    return (
        _engagement_out(services, engagement)
        if engagement
        else {"approval_request_id": approval_request_id}
    )


# ----- public: the candidate's secure link -----------------------------------------------------

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:620px;margin:6vh auto;padding:0 16px;
color:#1b2230;background:#fff}}label{{display:block;margin:12px 0 4px;font-weight:600}}
input,textarea{{width:100%;box-sizing:border-box;font:inherit;padding:8px;border:1px solid #ccd;
border-radius:6px}}.check{{display:flex;gap:8px;font-weight:400}}.check input{{width:auto}}
button{{margin-top:16px;padding:10px 18px;font:inherit;font-weight:600;border:0;border-radius:6px;
background:#2453c7;color:#fff}}.muted{{color:#5a6475}}</style></head><body>{body}</body></html>"""


def _error_page(message: str) -> HTMLResponse:
    body = f"<h1>Link not available</h1><p>{html.escape(message)}</p>"
    return HTMLResponse(_PAGE.format(title="Link not available", body=body), status_code=400)


@router.get("/collect/{token}", response_class=HTMLResponse)
def collect_page(token: str, services: ServicesDep) -> HTMLResponse:
    try:
        engagement, candidate, job = require_platform(services).resume_collection.open(token)
    except CollectionError as exc:
        return _error_page(str(exc))
    fields = "".join(
        f'<label for="{item}">{html.escape(LABELS[item])}</label>'
        f'<input id="{item}" name="{item}" value="{html.escape(getattr(candidate, item) or "")}">'
        for item in DETAIL_ITEMS
    )
    industry = html.escape(job.industry or "engineering")
    body = f"""
<h1>{html.escape(job.title)}</h1>
<p class="muted">Hi {html.escape(candidate.full_name.split()[0])}, please share the details
below so your recruiter can put you forward. Fields already filled in come from your
earlier messages.</p>
<form method="post">
<label for="resume_text">{LABELS["resume"]} (paste the text)</label>
<textarea id="resume_text" name="resume_text" rows="8"></textarea>
{fields}
<label class="check"><input type="checkbox" name="consent_store" value="yes" required>
I agree that CBS LLC may store my resume and details to find suitable roles for me.</label>
<label class="check"><input type="checkbox" name="consent_submit" value="yes" required>
I agree that CBS LLC may submit my resume for the {html.escape(job.title)} role
({html.escape(job.job_code)}) with a {industry} client. CBS LLC will tell me the client's name
and ask again before submitting me anywhere else.</label>
<button type="submit">Send to my recruiter</button>
</form>"""
    return HTMLResponse(_PAGE.format(title=html.escape(job.title), body=body))


@router.post("/collect/{token}", response_class=HTMLResponse)
def collect_submit(
    token: str,
    services: ServicesDep,
    resume_text: str = Form(default="", max_length=200_000),
    availability: str = Form(default="", max_length=300),
    visa_status: str = Form(default="", max_length=300),
    current_location: str = Form(default="", max_length=300),
    expected_rate: str = Form(default="", max_length=300),
    notice_period: str = Form(default="", max_length=300),
    work_authorization: str = Form(default="", max_length=300),
    preferred_location: str = Form(default="", max_length=300),
    consent_store: str = Form(default=""),
    consent_submit: str = Form(default=""),
) -> HTMLResponse:
    details = CollectedDetails(
        resume_text=resume_text or None,
        availability=availability, visa_status=visa_status, current_location=current_location,
        expected_rate=expected_rate, notice_period=notice_period,
        work_authorization=work_authorization, preferred_location=preferred_location,
        consent_store=consent_store == "yes", consent_submit=consent_submit == "yes",
    )  # fmt: skip
    try:
        engagement = require_platform(services).resume_collection.submit(token, details)
    except CollectionError as exc:
        return _error_page(str(exc))
    if engagement.missing:
        items = "".join(f"<li>{html.escape(LABELS[i])}</li>" for i in engagement.missing)
        body = (
            f"<h1>Thank you</h1><p>We still need:</p><ul>{items}</ul>"
            "<p>You can use the same link again.</p>"
        )
    else:
        body = "<h1>Thank you</h1><p>Your recruiter has everything needed and will be in touch.</p>"
    return HTMLResponse(_PAGE.format(title="Thank you", body=body))
