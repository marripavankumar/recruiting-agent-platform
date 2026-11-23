"""PostgreSQL delivery log (outreach_message table, migrations 0001 and 0003)."""

from __future__ import annotations

from datetime import datetime

from psycopg.types.json import Jsonb

from recruiting.db import Connect
from recruiting.domain import Channel
from recruiting.outreach.service import (
    AlreadySent,
    DeliveryReceipt,
    DeliveryResult,
    DeliveryStatus,
    OutboundMessage,
)

_SELECT = """
    SELECT approval_request_id::text, channel, recipient, content, delivery_status,
           provider_message_id, delivered_at, error
    FROM outreach_message WHERE approval_request_id = %s
"""


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
    request_id, channel, recipient, content, status, provider_id, delivered_at, error = row
    message = OutboundMessage(request_id, Channel(channel), recipient, dict(content))
    return DeliveryReceipt(message, DeliveryStatus(status), provider_id, delivered_at, error)
