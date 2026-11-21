"""Shared, channel-agnostic Outreach Service (Q7), used by Agents 3, 8, 9, 11 and 12.

Every send passes three checks before any channel adapter runs:
1. an APPROVED approval request whose content matches the message exactly;
2. the gate fits the channel (an email approval cannot be used for a LinkedIn request);
3. the recipient has not opted out.
Each approval authorises exactly one delivery.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from recruiting.approvals import ApprovalEngine
from recruiting.compliance import OptOutRegistry
from recruiting.domain import Channel, GateType

ALLOWED_GATES: dict[Channel, frozenset[GateType]] = {
    Channel.EMAIL: frozenset(
        {
            GateType.FIRST_CLIENT_EMAIL,
            GateType.CANDIDATE_OUTREACH_EMAIL,
            GateType.RESUME_SUBMISSION,
        }
    ),
    Channel.LINKEDIN: frozenset({GateType.LINKEDIN_CONNECTION_REQUEST}),
}


class DeliveryStatus(StrEnum):
    SENT = "sent"
    # Q15: LinkedIn requests are sent by the recruiter by hand; the platform queues and logs them.
    QUEUED_FOR_MANUAL_SEND = "queued_for_manual_send"


class OutreachError(Exception):
    pass


class RecipientOptedOut(OutreachError):
    pass


class NoActiveChannel(OutreachError):
    pass


class GateChannelMismatch(OutreachError):
    pass


class AlreadySent(OutreachError):
    pass


@dataclass(frozen=True)
class OutboundMessage:
    approval_request_id: str
    channel: Channel
    recipient: str
    content: Mapping[str, str]  # e.g. {"subject": ..., "body": ...}; must equal approved content


@dataclass(frozen=True)
class DeliveryResult:
    status: DeliveryStatus
    provider_message_id: str | None = None


@dataclass(frozen=True)
class DeliveryReceipt:
    message: OutboundMessage
    status: DeliveryStatus
    provider_message_id: str | None
    delivered_at: datetime


class ChannelAdapter(Protocol):
    channel: Channel

    def deliver(self, message: OutboundMessage) -> DeliveryResult: ...


class OutreachService:
    def __init__(
        self,
        approvals: ApprovalEngine,
        opt_outs: OptOutRegistry,
        channels: Iterable[ChannelAdapter],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._approvals = approvals
        self._opt_outs = opt_outs
        self._channels = {adapter.channel: adapter for adapter in channels}
        self._clock = clock
        self._receipts: dict[str, DeliveryReceipt] = {}

    @property
    def receipts(self) -> list[DeliveryReceipt]:
        return list(self._receipts.values())

    def send(self, message: OutboundMessage) -> DeliveryReceipt:
        request = self._approvals.assert_approved(message.approval_request_id, message.content)
        if request.gate not in ALLOWED_GATES[message.channel]:
            raise GateChannelMismatch(f"Gate {request.gate} cannot send on {message.channel}")
        if message.approval_request_id in self._receipts:
            raise AlreadySent(f"Approval {message.approval_request_id} was already used")
        if self._opt_outs.is_opted_out(message.recipient):
            raise RecipientOptedOut(f"{message.recipient} has opted out")
        adapter = self._channels.get(message.channel)
        if adapter is None:
            raise NoActiveChannel(f"No active adapter for channel {message.channel}")

        result = adapter.deliver(message)
        receipt = DeliveryReceipt(
            message=message,
            status=result.status,
            provider_message_id=result.provider_message_id,
            delivered_at=self._clock(),
        )
        self._receipts[message.approval_request_id] = receipt
        return receipt
