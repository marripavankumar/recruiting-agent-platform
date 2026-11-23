"""Inbound email webhook (Agent 4) and the public unsubscribe endpoints (checklist E6, E7)."""

from __future__ import annotations

import hmac
import html
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Form, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from recruiting.agents.jd_intake import InboundEmail
from recruiting.api.deps import ServicesDep, require_platform

router = APIRouter(tags=["inbound"])


class InboundEmailIn(BaseModel):
    from_address: str = Field(min_length=3, max_length=320)
    subject: str = Field(default="", max_length=1000)
    body: str = Field(default="", max_length=500_000)
    attachments_text: str = Field(default="", max_length=2_000_000)
    message_id: str = Field(min_length=1, max_length=500)
    received_at: datetime | None = None


@router.post("/inbound/email")
def receive_email(
    body: InboundEmailIn,
    services: ServicesDep,
    x_rap_webhook_secret: str | None = Header(default=None),
) -> dict:
    """Called by the mail-routing workflow (n8n) for each reply to the outreach mailbox."""
    platform = require_platform(services)
    expected = platform.inbound_webhook_secret
    if (
        not expected
        or not x_rap_webhook_secret
        or not hmac.compare_digest(expected, x_rap_webhook_secret)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook secret")
    email = InboundEmail(
        from_address=body.from_address.strip(),
        subject=body.subject,
        body=body.body,
        attachments_text=body.attachments_text,
        message_id=body.message_id,
        received_at=body.received_at or datetime.now(UTC),
    )
    # Replies from known candidates go to Agent 9; everything else to Agent 4.
    candidate = platform.candidates.find_duplicate(email.from_address.lower(), None, None)
    if candidate is not None:
        return {
            "handled_by": "agent-9",
            **asdict(platform.resume_collection.process_reply(email, candidate)),
        }
    return {"handled_by": "agent-4", **asdict(platform.jd_intake.process(email))}


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:520px;margin:12vh auto;padding:0 16px;
color:#1b2230;background:#fff}}</style></head><body><h1>{title}</h1><p>{text}</p>{form}</body></html>"""


def _opt_out(services: ServicesDep, token: str) -> str:
    platform = require_platform(services)
    address = platform.unsubscribe.verify(token)
    if address is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This unsubscribe link is not valid")
    services.opt_outs.record(address, source="unsubscribe_link")
    contact = platform.companies.contact_by_email(address)
    if contact is not None:
        from recruiting.records.models import OutreachStatus

        platform.companies.set_outreach_status(contact.company_id, OutreachStatus.DO_NOT_CONTACT)
    return address


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(
    services: ServicesDep, token: str = Query(min_length=10, max_length=600)
) -> str:
    """Link in the email footer. Asks for a click so that mail scanners following links do not
    unsubscribe people by accident."""
    if require_platform(services).unsubscribe.verify(token) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This unsubscribe link is not valid")
    form = (
        '<form method="post" action="/unsubscribe">'
        f'<input type="hidden" name="token" value="{html.escape(token)}">'
        '<button type="submit">Unsubscribe</button></form>'
    )
    return _PAGE.format(
        title="Unsubscribe", text="Stop all emails from CBS LLC to this address?", form=form
    )


@router.post("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(
    services: ServicesDep,
    token: str | None = Form(default=None),
    token_query: str | None = Query(default=None, alias="token"),
) -> str:
    """Form submit and RFC 8058 one-click (List-Unsubscribe-Post) both land here."""
    _opt_out(services, token or token_query or "")
    return _PAGE.format(
        title="You are unsubscribed",
        text="CBS LLC will not email this address again. This applies to every channel.",
        form="",
    )
