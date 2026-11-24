"""Reads the outreach mailbox and routes every new email (plan sections 7, 9 and 14).

A scheduled job (every 2 minutes) fetches unread messages from the same Gmail or Microsoft 365
mailbox the approved emails are sent from, and for each one:
- a bounce report (RFC 3464 delivery-status) goes to the bounce handler (S6), matched to the
  approval through the X-RAP-Approval-Id header returned with the original message;
- an automatic reply (Auto-Submitted, X-Autoreply ...) is routed as out-of-office (S9);
- any other email goes through the same router as the /inbound/email webhook (Agent 4 for
  clients, Agent 9 for candidates), with PDF / Word attachments read as text.
Each message is claimed by its Message-ID in a shared table before it is handled, so it is
handled once even with several scheduler replicas, and our own sent copies are ignored. A
message that fails is tried again on the next runs (3 attempts), then left for a person; the
operations view counts failures. The provider marks a message read only once it is finished.

Mailbox permissions: Gmail needs the gmail.modify scope; Microsoft 365 needs Mail.ReadWrite.
"""

from __future__ import annotations

import base64
import html
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, message_from_string, policy
from email.message import EmailMessage, Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import TYPE_CHECKING, Protocol

import httpx

from recruiting.agents.bounces import BounceError
from recruiting.agents.inbound_router import route_email
from recruiting.agents.jd_intake import InboundEmail
from recruiting.connectors.retry import call_with_retry
from recruiting.documents import DocumentError, extract_text
from recruiting.outreach.channels import APPROVAL_HEADER

if TYPE_CHECKING:
    from recruiting.api.platform import Platform
    from recruiting.db import Connect

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
STUCK_AFTER = timedelta(minutes=15)  # a claim left "processing" by a crashed worker


# ----- parsing ----------


@dataclass(frozen=True)
class BounceInfo:
    recipient: str
    hard: bool
    reason: str
    approval_request_id: str | None = None


@dataclass
class ParsedEmail:
    message_id: str
    from_address: str
    subject: str
    body: str
    received_at: datetime
    attachments: list[tuple[str, bytes]] = field(default_factory=list)
    auto_reply: bool = False
    bounce: BounceInfo | None = None
    looks_like_bounce: bool = False  # from a mail server, but no readable delivery report


_MAILER = re.compile(r"^(mailer-daemon|postmaster|mail-daemon)@", re.I)


def _html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", value)
    return html.unescape(re.sub(r"<[^>]+>", " ", value))


def _body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True) or b""
        text = payload.decode("utf-8", "replace")
    return _html_to_text(text) if part.get_content_type() == "text/html" else str(text)


def _auto_reply(msg: Message) -> bool:
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    return (msg.get("Precedence") or "").strip().lower() == "auto_reply"


def _delivery_status(msg: Message) -> BounceInfo | None:
    """RFC 3464: the per-recipient fields of a message/delivery-status part."""
    approval = None
    fields: dict[str, str] = {}
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "message/delivery-status":
            for block in part.get_payload() or []:
                if isinstance(block, Message):
                    for key in ("Final-Recipient", "Original-Recipient", "Action", "Status",
                                "Diagnostic-Code"):  # fmt: skip
                        if block.get(key) and key not in fields:
                            fields[key] = str(block.get(key))
        elif ctype == "message/rfc822":
            inner = part.get_payload()
            inner = inner[0] if isinstance(inner, list) and inner else inner
            if isinstance(inner, Message) and inner.get(APPROVAL_HEADER):
                approval = str(inner.get(APPROVAL_HEADER)).strip()
        elif ctype == "text/rfc822-headers":
            raw = part.get_payload(decode=True) or b""
            headers = message_from_string(raw.decode("utf-8", "replace"))
            if headers.get(APPROVAL_HEADER):
                approval = str(headers.get(APPROVAL_HEADER)).strip()
    recipient = fields.get("Final-Recipient") or fields.get("Original-Recipient")
    if not recipient:
        return None
    recipient = recipient.split(";", 1)[-1].strip().strip("<>")
    action = fields.get("Action", "").strip().lower()
    status = fields.get("Status", "").strip()
    hard = action == "failed" and status.startswith("5")
    reason = " ".join(x for x in (status, fields.get("Diagnostic-Code", "")) if x).strip()
    return BounceInfo(recipient, hard, reason or action or "bounced", approval)


def parse(raw: bytes, fallback_id: str, now: datetime) -> ParsedEmail:
    msg = message_from_bytes(raw, policy=policy.default)
    assert isinstance(msg, EmailMessage)
    sender = parseaddr(str(msg.get("From") or ""))[1].strip()
    try:
        received = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else now
    except (TypeError, ValueError):
        received = now
    if received.tzinfo is None:
        received = received.replace(tzinfo=UTC)
    parsed = ParsedEmail(
        message_id=str(msg.get("Message-ID") or f"<{fallback_id}@mailbox>").strip(),
        from_address=sender,
        subject=str(msg.get("Subject") or ""),
        body=_body(msg),
        received_at=received,
        auto_reply=_auto_reply(msg),
    )
    is_report = msg.get_content_type() == "multipart/report"
    if is_report or _MAILER.match(sender):
        parsed.bounce = _delivery_status(msg)
        parsed.looks_like_bounce = parsed.bounce is None
        return parsed
    for part in msg.iter_attachments():
        data = part.get_payload(decode=True)
        if isinstance(data, bytes) and data:
            parsed.attachments.append((part.get_filename() or "attachment", data))
    return parsed


# ----- which messages were handled ----------


class ProcessedStore(Protocol):
    def claim(self, key: str, now: datetime) -> int:
        """Attempt number (1, 2, ...) if this caller may handle the message now, else 0."""
        ...

    def finish(
        self, key: str, now: datetime, ok: bool, outcome: str, error: str | None
    ) -> None: ...

    def failures_since(self, since: datetime) -> int: ...


@dataclass
class _Entry:
    status: str
    attempts: int
    claimed_at: datetime
    outcome: str = ""
    error: str | None = None
    finished_at: datetime | None = None


class InMemoryProcessed:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def claim(self, key: str, now: datetime) -> int:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._entries[key] = _Entry("processing", 1, now)
                return 1
            retry = entry.status == "failed" and entry.attempts < MAX_ATTEMPTS
            stuck = entry.status == "processing" and now - entry.claimed_at > STUCK_AFTER
            if not (retry or stuck):
                return 0
            entry.status, entry.attempts, entry.claimed_at = "processing", entry.attempts + 1, now
            return entry.attempts

    def finish(self, key: str, now: datetime, ok: bool, outcome: str, error: str | None) -> None:
        with self._lock:
            entry = self._entries[key]
            entry.status, entry.outcome, entry.error = ("done" if ok else "failed"), outcome, error
            entry.finished_at = now

    def failures_since(self, since: datetime) -> int:
        with self._lock:
            return sum(
                1
                for e in self._entries.values()
                if e.status == "failed" and e.finished_at and e.finished_at >= since
            )


class PostgresProcessed:
    """inbound_message table (migration 0012)."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def claim(self, key: str, now: datetime) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO inbound_message (message_key, status, attempts, claimed_at)
                VALUES (%(key)s, 'processing', 1, %(now)s)
                ON CONFLICT (message_key) DO UPDATE
                    SET status = 'processing', attempts = inbound_message.attempts + 1,
                        claimed_at = EXCLUDED.claimed_at
                    WHERE (inbound_message.status = 'failed'
                           AND inbound_message.attempts < %(max)s)
                       OR (inbound_message.status = 'processing'
                           AND inbound_message.claimed_at < %(stuck)s)
                RETURNING attempts
                """,
                {"key": key, "now": now, "max": MAX_ATTEMPTS, "stuck": now - STUCK_AFTER},
            ).fetchone()
        return row[0] if row else 0

    def finish(self, key: str, now: datetime, ok: bool, outcome: str, error: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE inbound_message SET status = %s, outcome = %s, error = %s,"
                " finished_at = %s WHERE message_key = %s",
                ("done" if ok else "failed", outcome, error, now, key),
            )

    def failures_since(self, since: datetime) -> int:
        with self._connect() as conn:
            (count,) = conn.execute(
                "SELECT count(*) FROM inbound_message WHERE status = 'failed'"
                " AND finished_at >= %s",
                (since,),
            ).fetchone()
        return count


# ----- mailboxes ----------


@dataclass(frozen=True)
class MailItem:
    provider_id: str
    raw: bytes  # the full RFC 5322 message


class Mailbox(Protocol):
    def fetch(self, limit: int) -> list[MailItem]:
        """Unread messages in the inbox, oldest first where the provider allows."""
        ...

    def mark_done(self, provider_id: str) -> None: ...


TokenProvider = Callable[[], str]


class GmailMailbox:
    def __init__(self, token: TokenProvider, client: httpx.Client | None = None) -> None:
        self._token = token
        self._http = client or httpx.Client(base_url="https://gmail.googleapis.com", timeout=30)

    def _get(self, url: str, **params) -> dict:
        def call() -> dict:
            response = self._http.get(
                url, params=params, headers={"Authorization": f"Bearer {self._token()}"}
            )
            response.raise_for_status()
            return response.json()

        return call_with_retry(call)

    def fetch(self, limit: int) -> list[MailItem]:
        listing = self._get("/gmail/v1/users/me/messages", q="in:inbox is:unread", maxResults=limit)
        items = []
        for ref in reversed(listing.get("messages") or []):  # Gmail lists newest first
            message = self._get(f"/gmail/v1/users/me/messages/{ref['id']}", format="raw")
            raw = base64.urlsafe_b64decode(message["raw"] + "=" * (-len(message["raw"]) % 4))
            items.append(MailItem(ref["id"], raw))
        return items

    def mark_done(self, provider_id: str) -> None:
        response = self._http.post(
            f"/gmail/v1/users/me/messages/{provider_id}/modify",
            json={"removeLabelIds": ["UNREAD"]},
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        response.raise_for_status()


class Microsoft365Mailbox:
    def __init__(
        self, mailbox: str, token: TokenProvider, client: httpx.Client | None = None
    ) -> None:
        self._mailbox = mailbox
        self._token = token
        self._http = client or httpx.Client(base_url="https://graph.microsoft.com/v1.0", timeout=30)

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def fetch(self, limit: int) -> list[MailItem]:
        def listing() -> list[dict]:
            response = self._http.get(
                f"/users/{self._mailbox}/mailFolders/inbox/messages",
                params={
                    "$filter": "isRead eq false",
                    "$top": limit,
                    "$select": "id,receivedDateTime",
                },  # fmt: skip
                headers=self._auth(),
            )
            response.raise_for_status()
            return response.json().get("value") or []

        refs = sorted(call_with_retry(listing), key=lambda m: m.get("receivedDateTime") or "")
        items = []
        for ref in refs:

            def mime(ref: dict = ref) -> bytes:
                response = self._http.get(
                    f"/users/{self._mailbox}/messages/{ref['id']}/$value", headers=self._auth()
                )
                response.raise_for_status()
                return response.content

            items.append(MailItem(ref["id"], call_with_retry(mime)))
        return items

    def mark_done(self, provider_id: str) -> None:
        response = self._http.patch(
            f"/users/{self._mailbox}/messages/{provider_id}",
            json={"isRead": True},
            headers=self._auth(),
        )
        response.raise_for_status()


# ----- the reader ----------


@dataclass
class MailboxReport:
    fetched: int = 0
    routed: int = 0
    bounces: int = 0
    auto_replies: int = 0
    duplicates: int = 0
    own_messages: int = 0
    unreadable_bounces: int = 0
    failed: int = 0


class MailboxReader:
    def __init__(
        self,
        mailbox: Mailbox,
        platform: Platform,
        processed: ProcessedStore,
        own_addresses: tuple[str, ...] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        batch: int = 50,
    ) -> None:
        self._mailbox = mailbox
        self._platform = platform
        self._processed = processed
        self._own = {a.strip().lower() for a in own_addresses if a}
        self._clock = clock
        self._batch = batch

    def failures_since(self, since: datetime) -> int:
        return self._processed.failures_since(since)

    def run(self) -> MailboxReport:
        report = MailboxReport()
        for item in self._mailbox.fetch(self._batch):
            report.fetched += 1
            now = self._clock()
            try:
                parsed = parse(item.raw, item.provider_id, now)
                key = parsed.message_id
            except Exception:  # unparseable: record under the provider id
                parsed, key = None, f"<{item.provider_id}@mailbox>"
            attempt = self._processed.claim(key, now)
            if attempt == 0:
                report.duplicates += 1
                self._mailbox.mark_done(item.provider_id)
                continue
            try:
                if parsed is None:
                    raise ValueError("The message could not be parsed")
                outcome = self._handle(parsed, report)
            except Exception as exc:
                logger.exception("Inbound message %s failed (attempt %s)", key, attempt)
                report.failed += 1
                error = f"{type(exc).__name__}: {exc}"[:500]
                self._processed.finish(key, self._clock(), False, "failed", error)
                if attempt >= MAX_ATTEMPTS:
                    self._mailbox.mark_done(item.provider_id)  # leave it to a person
                continue
            self._processed.finish(key, self._clock(), True, outcome, None)
            self._mailbox.mark_done(item.provider_id)
        return report

    def _handle(self, parsed: ParsedEmail, report: MailboxReport) -> str:
        if parsed.from_address.lower() in self._own:
            report.own_messages += 1
            return "own_message"
        if parsed.bounce is not None:
            bounce = parsed.bounce
            try:
                self._platform.bounces.process(
                    bounce.recipient, bounce.reason, hard=bounce.hard,
                    approval_request_id=bounce.approval_request_id,
                )  # fmt: skip
            except BounceError:  # approval id unknown here: match by recipient instead
                self._platform.bounces.process(bounce.recipient, bounce.reason, hard=bounce.hard)
            report.bounces += 1
            return "bounce"
        if parsed.looks_like_bounce:
            report.unreadable_bounces += 1
            return "unreadable_bounce"  # no recipient to act on; visible in the mailbox
        texts = []
        for name, data in parsed.attachments:
            try:
                doc = extract_text(name, data)
            except DocumentError as exc:
                logger.info("Attachment %s in %s not read: %s", name, parsed.message_id, exc)
                continue
            texts.append(f"--- {doc.filename} ---\n{doc.text}")
        email = InboundEmail(
            from_address=parsed.from_address,
            subject=parsed.subject,
            body=parsed.body,
            received_at=parsed.received_at,
            message_id=parsed.message_id,
            attachments_text="\n\n".join(texts),
            auto_reply=parsed.auto_reply,
        )
        result = route_email(self._platform, email)
        if parsed.auto_reply:
            report.auto_replies += 1
        report.routed += 1
        return f"{result['handled_by']}:{result.get('category', '')}"
