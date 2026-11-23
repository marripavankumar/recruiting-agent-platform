"""Shared, channel-agnostic Outreach Service (Q7), used by Agents 3, 8, 9, 11 and 12.

Every send passes three checks before any channel adapter runs:
1. an APPROVED approval request whose content matches the message exactly;
2. the gate fits the channel (an email approval cannot be used for a LinkedIn request);
3. the recipient has not opted out;
then the environment must allow outreach to that recipient (environment plan section 2).
Each approval authorises exactly one delivery: the delivery log claims the approval before the
channel adapter runs, so a retry or a concurrent send cannot deliver twice. A failed delivery
keeps its claim (the provider may have sent it anyway); sending again needs a new approval.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
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
    SENDING = "sending"  # claimed; the channel adapter is running
    SENT = "sent"
    # Q15: LinkedIn requests are sent by the recruiter by hand; the platform queues and logs them.
    QUEUED_FOR_MANUAL_SEND = "queued_for_manual_send"
    FAILED = "failed"


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


class OutreachDisabled(OutreachError):
    """Outreach is off in this environment, or the recipient is not on its allow-list."""


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
    error: str | None = None


class DeliveryLog(Protocol):
    def claim(self, message: OutboundMessage, at: datetime) -> None:
        """Record a SENDING entry; raise AlreadySent if the approval already has one."""
        ...

    def complete(
        self, approval_request_id: str, result: DeliveryResult, at: datetime
    ) -> DeliveryReceipt: ...

    def fail(self, approval_request_id: str, error: str, at: datetime) -> DeliveryReceipt: ...

    def get(self, approval_request_id: str) -> DeliveryReceipt | None: ...


class InMemoryDeliveryLog:
    """Single-process use only (tests, quick local runs)."""

    def __init__(self) -> None:
        self._receipts: dict[str, DeliveryReceipt] = {}

    def claim(self, message: OutboundMessage, at: datetime) -> None:
        if message.approval_request_id in self._receipts:
            raise AlreadySent(f"Approval {message.approval_request_id} was already used")
        self._receipts[message.approval_request_id] = DeliveryReceipt(
            message, DeliveryStatus.SENDING, None, at
        )

    def complete(
        self, approval_request_id: str, result: DeliveryResult, at: datetime
    ) -> DeliveryReceipt:
        receipt = replace(
            self._receipts[approval_request_id],
            status=result.status,
            provider_message_id=result.provider_message_id,
            delivered_at=at,
        )
        self._receipts[approval_request_id] = receipt
        return receipt

    def fail(self, approval_request_id: str, error: str, at: datetime) -> DeliveryReceipt:
        receipt = replace(
            self._receipts[approval_request_id],
            status=DeliveryStatus.FAILED,
            delivered_at=at,
            error=error,
        )
        self._receipts[approval_request_id] = receipt
        return receipt

    def get(self, approval_request_id: str) -> DeliveryReceipt | None:
        return self._receipts.get(approval_request_id)


class ChannelAdapter(Protocol):
    channel: Channel

    def deliver(self, message: OutboundMessage) -> DeliveryResult: ...


class OutreachService:
    def __init__(
        self,
        approvals: ApprovalEngine,
        opt_outs: OptOutRegistry,
        channels: Iterable[ChannelAdapter],
        *,
        log: DeliveryLog | None = None,
        enabled: bool = True,
        allow_list: Iterable[str] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._approvals = approvals
        self._opt_outs = opt_outs
        self._channels = {adapter.channel: adapter for adapter in channels}
        self._log = InMemoryDeliveryLog() if log is None else log
        self._enabled = enabled
        # Empty means every recipient; otherwise only these (test environment).
        self._allow_list = frozenset(OptOutRegistry.normalize(a) for a in allow_list)
        self._clock = clock

    def receipt(self, approval_request_id: str) -> DeliveryReceipt | None:
        return self._log.get(approval_request_id)

    def send(self, message: OutboundMessage) -> DeliveryReceipt:
        request = self._approvals.assert_approved(message.approval_request_id, message.content)
        if request.gate not in ALLOWED_GATES[message.channel]:
            raise GateChannelMismatch(f"Gate {request.gate} cannot send on {message.channel}")
        if self._opt_outs.is_opted_out(message.recipient):
            raise RecipientOptedOut(f"{message.recipient} has opted out")
        if not self._enabled:
            raise OutreachDisabled("Outreach is disabled in this environment")
        if self._allow_list and OptOutRegistry.normalize(message.recipient) not in self._allow_list:
            raise OutreachDisabled(f"{message.recipient} is not on this environment's allow-list")
        adapter = self._channels.get(message.channel)
        if adapter is None:
            raise NoActiveChannel(f"No active adapter for channel {message.channel}")

        self._log.claim(message, self._clock())
        try:
            result = adapter.deliver(message)
        except Exception as exc:
            self._log.fail(
                message.approval_request_id, f"{type(exc).__name__}: {exc}"[:500], self._clock()
            )
            raise
        return self._log.complete(message.approval_request_id, result, self._clock())
