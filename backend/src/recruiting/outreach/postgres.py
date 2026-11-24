"""PostgreSQL delivery log (outreach_message table, migrations 0001 and 0003)."""

from __future__ import annotations

from datetime import datetime

from psycopg.types.json import Jsonb

from recruiting.db import Connect
from recruiting.domain import Channel
from recruiting.outreach.service import (
    COUNTED,
    AlreadySent,
    DeliveryReceipt,
    DeliveryResult,
    DeliveryStatus,
    OutboundMessage,
)

_COLUMNS = """approval_request_id::text, channel, recipient, content, delivery_status,
    provider_message_id, delivered_at, error, opened_at, replied_at"""
_SELECT = f"SELECT {_COLUMNS} FROM outreach_message WHERE approval_request_id = %s"
_COUNTED = tuple(s.value for s in COUNTED)


class PostgresDeliveryLog:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def claim(self, message: OutboundMessage, at: datetime) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO outreach_message
                    (approval_request_id, channel, recipient, content, delivery_status,
                     delivered_at)
                VALUES (%s, %s, %s, %s, 'sending', %s)
                ON CONFLICT (approval_request_id) DO NOTHING
                RETURNING id
                """,
                (
                    message.approval_request_id,
                    message.channel.value,
                    message.recipient,
                    Jsonb(dict(message.content)),
                    at,
                ),
            ).fetchone()
        if row is None:
            raise AlreadySent(f"Approval {message.approval_request_id} was already used")

    def complete(
        self, approval_request_id: str, result: DeliveryResult, at: datetime
    ) -> DeliveryReceipt:
        return self._update(
            approval_request_id, result.status, result.provider_message_id, None, at
        )

    def fail(self, approval_request_id: str, error: str, at: datetime) -> DeliveryReceipt:
        return self._update(approval_request_id, DeliveryStatus.FAILED, None, error, at)

    def get(self, approval_request_id: str) -> DeliveryReceipt | None:
        with self._connect() as conn:
            row = conn.execute(_SELECT, (approval_request_id,)).fetchone()
        return _receipt(row) if row else None

    def sent_count_since(self, channel: Channel, since: datetime) -> int:
        with self._connect() as conn:
            (count,) = conn.execute(
                "SELECT count(*) FROM outreach_message WHERE channel = %s"
                " AND delivery_status = ANY(%s) AND delivered_at >= %s",
                (channel.value, list(_COUNTED), since),
            ).fetchone()
        return count

    def latest_to(self, recipient: str) -> DeliveryReceipt | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM outreach_message"
                " WHERE lower(rtrim(recipient, '/')) = lower(rtrim(%s, '/'))"
                " ORDER BY delivered_at DESC LIMIT 1",
                (recipient.strip(),),
            ).fetchone()
        return _receipt(row) if row else None

    def record_open(self, approval_request_id: str, at: datetime) -> None:
        self._mark("opened_at", approval_request_id, at)

    def record_reply(self, approval_request_id: str, at: datetime) -> None:
        self._mark("replied_at", approval_request_id, at)

    def _mark(self, column: str, approval_request_id: str, at: datetime) -> None:
        assert column in ("opened_at", "replied_at")
        with self._connect() as conn:
            conn.execute(
                f"UPDATE outreach_message SET {column} = coalesce({column}, %s)"
                " WHERE approval_request_id = %s",
                (at, approval_request_id),
            )

    def receipts(self, since: datetime | None = None) -> list[DeliveryReceipt]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM outreach_message"
                " WHERE %(since)s::timestamptz IS NULL OR delivered_at >= %(since)s"
                " ORDER BY delivered_at",
                {"since": since},
            ).fetchall()
        return [_receipt(r) for r in rows]

    def record_bounce(self, approval_request_id: str, reason: str) -> DeliveryReceipt:
        # Keeps the provider id and send time; only the outcome changes.
        with self._connect() as conn:
            conn.execute(
                "UPDATE outreach_message SET delivery_status = 'bounced', error = %s"
                " WHERE approval_request_id = %s",
                (reason, approval_request_id),
            )
            conn.execute(
                """
                INSERT INTO audit_log (actor, action, entity_type, entity_id, details)
                VALUES ('outreach_service', 'outreach.bounced', 'approval_request', %s, %s)
                """,
                (approval_request_id, Jsonb({"error": reason})),
            )
            row = conn.execute(_SELECT, (approval_request_id,)).fetchone()
        return _receipt(row)

    def mark_manually_sent(self, approval_request_id: str, at: datetime) -> DeliveryReceipt:
        return self._update(approval_request_id, DeliveryStatus.MANUALLY_SENT, None, None, at)

    def _update(
        self,
        approval_request_id: str,
        status: DeliveryStatus,
        provider_message_id: str | None,
        error: str | None,
        at: datetime,
    ) -> DeliveryReceipt:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE outreach_message
                SET delivery_status = %s, provider_message_id = %s, error = %s, delivered_at = %s
                WHERE approval_request_id = %s
                """,
                (status.value, provider_message_id, error, at, approval_request_id),
            )
            conn.execute(
                """
                INSERT INTO audit_log (actor, action, entity_type, entity_id, details)
                VALUES ('outreach_service', %s, 'approval_request', %s, %s)
                """,
                (f"outreach.{status.value}", approval_request_id, Jsonb({"error": error})),
            )
            row = conn.execute(_SELECT, (approval_request_id,)).fetchone()
        return _receipt(row)


def _receipt(row: tuple) -> DeliveryReceipt:
    (request_id, channel, recipient, content, status, provider_id, delivered_at, error, opened,
     replied) = row  # fmt: skip
    message = OutboundMessage(request_id, Channel(channel), recipient, dict(content))
    return DeliveryReceipt(
        message, DeliveryStatus(status), provider_id, delivered_at, error, opened, replied
    )
