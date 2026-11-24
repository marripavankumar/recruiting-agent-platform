"""Shared, channel-agnostic Outreach Service (Q7), used by Agents 3, 8, 9, 11 and 12.

Every send passes three checks before any channel adapter runs:
1. an APPROVED approval request whose content matches the message exactly;
2. the gate fits the channel (an email approval cannot be used for a LinkedIn request);
3. the recipient has not opted out;
then the environment must allow outreach to that recipient (environment plan section 2).
Each approval authorises exactly one delivery: the delivery log claims the approval before the
channel adapter runs, so a retry or a concurrent send cannot deliver twice. A failed delivery
keeps its claim (the provider may have sent it anyway); sending again needs a new approval.

Scenario S24: when the provider certainly did not take the message (the adapter raises
TransientDeliveryError: connection refused, rate limited, temporarily unavailable) or the daily
cap is used up, the delivery is RETRY_SCHEDULED with exponential back-off instead of failing.
`retry_due()` (run on a schedule) sends it again under the same approval, re-checking the
approval, opt-outs and environment first; after `max_attempts` it fails and is alerted. Errors
where the message may have gone out (a timeout after sending, a 500) are never retried.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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
    MANUALLY_SENT = "manually_sent"  # the recruiter confirmed sending it in LinkedIn
    BOUNCED = "bounced"  # accepted by the provider, then returned as undeliverable (S6)
    RETRY_SCHEDULED = "retry_scheduled"  # S24: not sent yet; tried again at next_attempt_at
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


class DeliveryDeferred(OutreachError):
    """Not sent now; the delivery is RETRY_SCHEDULED under the same approval (S24)."""

    def __init__(self, message: str, receipt: DeliveryReceipt | None = None) -> None:
        super().__init__(message)
        self.receipt = receipt


class DailyCapReached(DeliveryDeferred):
    """The channel's daily send cap is used up (protects the sending domain's reputation).

    The delivery is scheduled for the start of the next day under the same approval.
    """


class TransientDeliveryError(Exception):
    """Raised by a channel adapter when the provider certainly did not accept the message, so
    sending it again cannot produce a duplicate (S24)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after  # seconds, from the provider's Retry-After header


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
    opened_at: datetime | None = None
    replied_at: datetime | None = None
    attempts: int = 0  # failed attempts that were retried (S24)
    next_attempt_at: datetime | None = None


@dataclass
class RetryReport:
    due: int = 0
    sent: int = 0
    deferred: int = 0
    failed: int = 0


# Statuses that count against a daily cap: everything that went, or is going, out.
COUNTED = frozenset(
    {
        DeliveryStatus.SENDING,
        DeliveryStatus.SENT,
        DeliveryStatus.QUEUED_FOR_MANUAL_SEND,
        DeliveryStatus.MANUALLY_SENT,
    }
)


class DeliveryLog(Protocol):
    def claim(self, message: OutboundMessage, at: datetime) -> None:
        """Record a SENDING entry; raise AlreadySent if the approval already has one, unless
        that entry is RETRY_SCHEDULED (S24), which is claimed again."""
        ...

    def defer(
        self,
        message: OutboundMessage,
        error: str,
        retry_at: datetime,
        at: datetime,
        *,
        claimed: bool,
        count_attempt: bool,
    ) -> DeliveryReceipt:
        """S24: mark the delivery RETRY_SCHEDULED. `claimed`: this caller holds the SENDING
        claim; otherwise the entry must be new or already scheduled. Raise AlreadySent if not."""
        ...

    def due_retries(self, now: datetime) -> list[DeliveryReceipt]: ...

    def complete(
        self, approval_request_id: str, result: DeliveryResult, at: datetime
    ) -> DeliveryReceipt: ...

    def fail(self, approval_request_id: str, error: str, at: datetime) -> DeliveryReceipt: ...

    def get(self, approval_request_id: str) -> DeliveryReceipt | None: ...

    def sent_count_since(self, channel: Channel, since: datetime) -> int: ...

    def latest_to(self, recipient: str) -> DeliveryReceipt | None:
        """Most recent delivery to this address (used to match replies)."""
        ...

    def record_open(self, approval_request_id: str, at: datetime) -> None: ...

    def record_reply(self, approval_request_id: str, at: datetime) -> None: ...

    def record_bounce(self, approval_request_id: str, reason: str) -> DeliveryReceipt:
        """S6: the provider reported the sent message as undeliverable."""
        ...

    def receipts(self, since: datetime | None = None) -> list[DeliveryReceipt]: ...

    def mark_manually_sent(self, approval_request_id: str, at: datetime) -> DeliveryReceipt:
        """Q15: the recruiter sent the approved LinkedIn note by hand."""
        ...


class InMemoryDeliveryLog:
    """Single-process use only (tests, quick local runs)."""

    def __init__(self) -> None:
        self._receipts: dict[str, DeliveryReceipt] = {}

    def claim(self, message: OutboundMessage, at: datetime) -> None:
        existing = self._receipts.get(message.approval_request_id)
        if existing is not None and existing.status is not DeliveryStatus.RETRY_SCHEDULED:
            raise AlreadySent(f"Approval {message.approval_request_id} was already used")
        self._receipts[message.approval_request_id] = (
            replace(existing, status=DeliveryStatus.SENDING, delivered_at=at, next_attempt_at=None)
            if existing is not None
            else DeliveryReceipt(message, DeliveryStatus.SENDING, None, at)
        )

    def defer(
        self,
        message: OutboundMessage,
        error: str,
        retry_at: datetime,
        at: datetime,
        *,
        claimed: bool,
        count_attempt: bool,
    ) -> DeliveryReceipt:
        existing = self._receipts.get(message.approval_request_id)
        allowed = (
            existing is not None and existing.status is DeliveryStatus.SENDING
            if claimed
            else existing is None or existing.status is DeliveryStatus.RETRY_SCHEDULED
        )
        if not allowed:
            raise AlreadySent(f"Approval {message.approval_request_id} was already used")
        base = existing or DeliveryReceipt(message, DeliveryStatus.RETRY_SCHEDULED, None, at)
        receipt = replace(
            base,
            status=DeliveryStatus.RETRY_SCHEDULED,
            error=error,
            delivered_at=at,
            next_attempt_at=retry_at,
            attempts=base.attempts + int(count_attempt),
        )
        self._receipts[message.approval_request_id] = receipt
        return receipt

    def due_retries(self, now: datetime) -> list[DeliveryReceipt]:
        return sorted(
            (
                r
                for r in self._receipts.values()
                if r.status is DeliveryStatus.RETRY_SCHEDULED
                and r.next_attempt_at is not None
                and r.next_attempt_at <= now
            ),
            key=lambda r: r.next_attempt_at or now,
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

    def sent_count_since(self, channel: Channel, since: datetime) -> int:
        return sum(
            1
            for r in self._receipts.values()
            if r.message.channel == channel and r.status in COUNTED and r.delivered_at >= since
        )

    def latest_to(self, recipient: str) -> DeliveryReceipt | None:
        wanted = OptOutRegistry.normalize(recipient)
        matches = [
            r
            for r in self._receipts.values()
            if OptOutRegistry.normalize(r.message.recipient) == wanted
        ]
        return max(matches, key=lambda r: r.delivered_at, default=None)

    def record_open(self, approval_request_id: str, at: datetime) -> None:
        receipt = self._receipts[approval_request_id]
        if receipt.opened_at is None:
            self._receipts[approval_request_id] = replace(receipt, opened_at=at)

    def record_reply(self, approval_request_id: str, at: datetime) -> None:
        receipt = self._receipts[approval_request_id]
        if receipt.replied_at is None:
            self._receipts[approval_request_id] = replace(receipt, replied_at=at)

    def record_bounce(self, approval_request_id: str, reason: str) -> DeliveryReceipt:
        receipt = replace(
            self._receipts[approval_request_id], status=DeliveryStatus.BOUNCED, error=reason
        )
        self._receipts[approval_request_id] = receipt
        return receipt

    def receipts(self, since: datetime | None = None) -> list[DeliveryReceipt]:
        return [r for r in self._receipts.values() if since is None or r.delivered_at >= since]

    def mark_manually_sent(self, approval_request_id: str, at: datetime) -> DeliveryReceipt:
        receipt = replace(
            self._receipts[approval_request_id],
            status=DeliveryStatus.MANUALLY_SENT,
            delivered_at=at,
        )
        self._receipts[approval_request_id] = receipt
        return receipt


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
        daily_caps: Mapping[Channel, int] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_attempts: int = 5,
        backoff: timedelta = timedelta(minutes=1),
        max_backoff: timedelta = timedelta(hours=6),
    ) -> None:
        # S24: retry after 1, 4, 16, 64 minutes ... (or the provider's Retry-After), at most
        # max_attempts tries in all, then fail and alert.
        self._max_attempts = max_attempts
        self._backoff = backoff
        self._max_backoff = max_backoff
        self._approvals = approvals
        self._opt_outs = opt_outs
        self._channels = {adapter.channel: adapter for adapter in channels}
        self._log = InMemoryDeliveryLog() if log is None else log
        self._enabled = enabled
        # Empty means every recipient; otherwise only these (test environment).
        self._allow_list = frozenset(OptOutRegistry.normalize(a) for a in allow_list)
        self._daily_caps = dict(daily_caps or {})
        self._clock = clock

    @property
    def log(self) -> DeliveryLog:
        return self._log

    def receipt(self, approval_request_id: str) -> DeliveryReceipt | None:
        return self._log.get(approval_request_id)

    def remaining_today(self, channel: Channel) -> int | None:
        cap = self._daily_caps.get(channel)
        if cap is None:
            return None
        now = self._clock()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return max(cap - self._log.sent_count_since(channel, start), 0)

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
        # Soft cap: two sends racing at the limit may both pass; the claim below still
        # guarantees each approval delivers at most once.
        if self.remaining_today(message.channel) == 0:
            now = self._clock()
            tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            receipt = self._log.defer(
                message, "Daily cap reached", tomorrow, now, claimed=False, count_attempt=False
            )
            raise DailyCapReached(f"Daily {message.channel} cap reached; retry tomorrow", receipt)

        self._log.claim(message, self._clock())
        try:
            result = adapter.deliver(message)
        except TransientDeliveryError as exc:
            deferred = self._retry_later(message, exc)
            if deferred is None:
                raise  # out of attempts: recorded as failed
            raise deferred from exc
        except Exception as exc:
            self._log.fail(
                message.approval_request_id, f"{type(exc).__name__}: {exc}"[:500], self._clock()
            )
            raise
        return self._log.complete(message.approval_request_id, result, self._clock())

    def _retry_later(
        self, message: OutboundMessage, exc: TransientDeliveryError
    ) -> DeliveryDeferred | None:
        now = self._clock()
        previous = self._log.get(message.approval_request_id)
        attempts = (previous.attempts if previous else 0) + 1
        error = f"{type(exc).__name__}: {exc}"[:450]
        if attempts >= self._max_attempts:
            self._log.fail(
                message.approval_request_id, f"Gave up after {attempts} attempts: {error}", now
            )
            return None
        delay = self._backoff * 4 ** (attempts - 1)
        if exc.retry_after is not None:
            delay = max(delay, timedelta(seconds=exc.retry_after))
        receipt = self._log.defer(
            message, error, now + min(delay, self._max_backoff), now,
            claimed=True, count_attempt=True,
        )  # fmt: skip
        return DeliveryDeferred(f"Delivery deferred: {error}", receipt)

    def retry_due(self) -> RetryReport:
        """S24: send every delivery whose retry time has come (run on a schedule)."""
        report = RetryReport()
        for receipt in self._log.due_retries(self._clock()):
            report.due += 1
            request_id = receipt.message.approval_request_id
            try:
                self.send(receipt.message)
                report.sent += 1
            except DeliveryDeferred:
                report.deferred += 1
            except Exception as exc:
                # Opted out, approval withdrawn, outreach switched off, or the provider refused
                # it for good: stop retrying and leave it to a person.
                current = self._log.get(request_id)
                if current is not None and current.status is DeliveryStatus.RETRY_SCHEDULED:
                    self._log.fail(
                        request_id, f"Not retried: {type(exc).__name__}: {exc}"[:500],
                        self._clock(),
                    )  # fmt: skip
                report.failed += 1
        return report
