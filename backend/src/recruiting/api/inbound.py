"""Inbound email webhook (Agent 4) and the public unsubscribe endpoints (checklist E6, E7)."""

from __future__ import annotations

import base64
import binascii
import hmac
import html
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Form, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from recruiting.agents.bounces import BounceError
from recruiting.agents.inbound_router import route_email
from recruiting.agents.jd_intake import InboundEmail, attachments_text, read_attachments
from recruiting.api.deps import ServicesDep, require_platform
from recruiting.api.platform import Platform

router = APIRouter(tags=["inbound"])


class AttachmentIn(BaseModel):
    """A raw attachment (PDF, Word .docx or text); the platform extracts the text."""

    filename: str = Field(default="attachment", max_length=255)
    content_base64: str = Field(min_length=1, max_length=14_000_000)  # about 10 MB decoded


class InboundEmailIn(BaseModel):
    from_address: str = Field(min_length=3, max_length=320)
    subject: str = Field(default="", max_length=1000)
    body: str = Field(default="", max_length=500_000)
    # Text the mail-routing workflow already extracted, and/or the raw files.
    attachments_text: str = Field(default="", max_length=2_000_000)
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=10)
    message_id: str = Field(min_length=1, max_length=500)
    received_at: datetime | None = None


def _attachments(attachments: list[AttachmentIn]):
    """The readable attachments (file and text) and an error per file that is not."""
    decoded, errors = [], []
    for item in attachments:
        try:
            decoded.append((item.filename, base64.b64decode(item.content_base64, validate=True)))
        except binascii.Error:
            errors.append({"filename": item.filename, "error": "Not valid base64 content."})
    files, unreadable = read_attachments(decoded)
    return files, errors + unreadable


@router.post("/inbound/email")
def receive_email(
    body: InboundEmailIn,
    services: ServicesDep,
    x_rap_webhook_secret: str | None = Header(default=None),
) -> dict:
    """Called by the mail-routing workflow (n8n) for each reply to the outreach mailbox."""
    platform = require_platform(services)
    _check_secret(platform, x_rap_webhook_secret)
    files, attachment_errors = _attachments(body.attachments)
    extracted = attachments_text(files)
    email = InboundEmail(
        from_address=body.from_address.strip(),
        subject=body.subject,
        body=body.body,
        attachments_text="\n\n".join(t for t in (body.attachments_text, extracted) if t.strip()),
        message_id=body.message_id,
        received_at=body.received_at or datetime.now(UTC),
        files=files,
    )
    # Unreadable files are reported so the mail-routing workflow can flag them to a person.
    return {**route_email(platform, email), "attachment_errors": attachment_errors}


class BounceIn(BaseModel):
    recipient: str = Field(min_length=3, max_length=320)
    reason: str = Field(default="", max_length=2000)
    hard: bool = True  # permanent failure (5.x.x); soft bounces are left to provider retries
    approval_request_id: str | None = Field(default=None, max_length=100)


@router.post("/inbound/bounce")
def receive_bounce(
    body: BounceIn,
    services: ServicesDep,
    x_rap_webhook_secret: str | None = Header(default=None),
) -> dict:
    """Scenario S6: the mail-routing workflow forwards each bounce notice from the provider."""
    platform = require_platform(services)
    _check_secret(platform, x_rap_webhook_secret)
    try:
        result = platform.bounces.process(
            body.recipient, body.reason, hard=body.hard,
            approval_request_id=body.approval_request_id,
        )  # fmt: skip
    except BounceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return asdict(result)


def _check_secret(platform: Platform, given: str | None) -> None:
    expected = platform.inbound_webhook_secret
    if not expected or not given or not hmac.compare_digest(expected, given):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook secret")


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
