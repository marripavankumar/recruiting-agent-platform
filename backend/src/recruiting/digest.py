"""Daily reminder digest for staff (Agent 12, plan section 11).

Once a day, at RAP_DIGEST_HOUR in RAP_DIGEST_TIMEZONE (weekdays unless RAP_DIGEST_WEEKENDS),
each active staff user gets one email listing their follow-ups that are overdue or due before
the end of their day. Users with nothing due get nothing. The Administrator, who owns no
follow-ups, gets the overdue items across everyone, the items that would otherwise stall
unseen.

The digest is internal mail to platform users, not outreach: it goes straight to the email
adapter, without an approval gate, an unsubscribe link (one click would opt the staff member
out of outreach) or the X-RAP-Approval-Id header. Each user is claimed per day in a shared store
(PostgreSQL: migration 0014), so scheduler replicas and restarts never send a digest twice; a
failed send releases the claim and is retried on the next run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

from recruiting.domain import Channel, Role
from recruiting.outreach.service import ChannelAdapter, DeliveryStatus, OutboundMessage

if TYPE_CHECKING:
    from recruiting.db import Connect
    from recruiting.identity.users import CurrentUser, UserDirectory
    from recruiting.records.models import FollowUp

logger = logging.getLogger(__name__)

MAX_LINES = 25  # longer lists end with "... and N more" and the link to the full list
KIND_LABELS = {
    "client_outreach": "Client email",
    "submission_feedback": "Submission feedback",
    "candidate_outreach": "Candidate reply",
    "candidate_details": "Candidate details",
    "interview_scheduling": "Interview scheduling",
    "interview_prep": "Interview preparation",
    "interview_feedback": "Interview feedback",
    "offer": "Offer",
    "start_date": "Start date",
    "candidate_shortfall": "Too few candidates",
    "jd_promised": "Job description promised",
}


class FollowUpSource(Protocol):
    def for_user(self, role: str, user_id: str | None) -> list[FollowUp]: ...


class DigestLog(Protocol):
    def claim(self, user_id: str, day: date, now: datetime) -> bool:
        """Atomically record that this user's digest for `day` is being sent; False if it was."""
        ...

    def release(self, user_id: str, day: date) -> None:
        """Forget a claim whose send failed, so the next run tries again."""
        ...


class InMemoryDigestLog:
    def __init__(self) -> None:
        self._sent: set[tuple[str, date]] = set()

    def claim(self, user_id: str, day: date, now: datetime) -> bool:
        if (user_id, day) in self._sent:
            return False
        self._sent.add((user_id, day))
        return True

    def release(self, user_id: str, day: date) -> None:
        self._sent.discard((user_id, day))


class PostgresDigestLog:
    """reminder_digest table (migration 0014); shared by every scheduler replica."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def claim(self, user_id: str, day: date, now: datetime) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "INSERT INTO reminder_digest (user_id, digest_date, sent_at) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING RETURNING user_id",
                (user_id, day, now),
            ).fetchone()
        return row is not None

    def release(self, user_id: str, day: date) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM reminder_digest WHERE user_id = %s AND digest_date = %s",
                (user_id, day),
            )


@dataclass
class DigestReport:
    sent: int = 0
    nothing_due: int = 0
    already_sent: int = 0
    failed: int = 0
    not_time_yet: int = 0  # 1 when the run was before the digest hour or on a weekend


class DigestFailed(Exception):
    """Some digests could not be sent; raised after the others went out, so the scheduler
    records the run as failed and the operations view shows the alert."""


class ReminderDigest:
    def __init__(
        self,
        follow_ups: FollowUpSource,
        users: UserDirectory,
        channel: ChannelAdapter,
        log: DigestLog,
        *,
        hour: int = 7,
        timezone: str = "America/New_York",
        weekends: bool = False,
        web_base_url: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if channel.channel is not Channel.EMAIL:
            raise ValueError("The reminder digest needs an email channel")
        if not 0 <= hour <= 23:
            raise ValueError("RAP_DIGEST_HOUR must be between 0 and 23")
        self._follow_ups = follow_ups
        self._users = users
        self._channel = channel
        self._log = log
        self._hour = hour
        self._zone = ZoneInfo(timezone)
        self._weekends = weekends
        self._link = f"{web_base_url.rstrip('/')}/follow-ups" if web_base_url else None
        self._clock = clock

    def run(self) -> DigestReport:
        """Send today's digests that are due and not yet sent. Safe to call as often as wanted."""
        now = self._clock()
        local = now.astimezone(self._zone)
        report = DigestReport()
        if local.hour < self._hour or (not self._weekends and local.weekday() >= 5):
            report.not_time_yet = 1
            return report
        end_of_day = datetime.combine(local.date() + timedelta(days=1), time(), self._zone)
        failures: list[str] = []
        for user in self._recipients():
            items = self._items(user, now, end_of_day)
            if not items:
                report.nothing_due += 1
                continue
            if not self._log.claim(user.user_id, local.date(), now):
                report.already_sent += 1
                continue
            message = OutboundMessage(
                approval_request_id="",  # not an approved message: no approval header
                channel=Channel.EMAIL,
                recipient=user.email,
                content=self.render(user, items, now, local.date()),
            )
            try:
                result = self._channel.deliver(message)
                if result.status is not DeliveryStatus.SENT:
                    raise RuntimeError(f"delivery status {result.status.value}")
            except Exception as exc:  # one failed mailbox must not stop the others
                logger.warning("Reminder digest to user %s failed: %s", user.user_id, exc)
                self._log.release(user.user_id, local.date())
                report.failed += 1
                failures.append(type(exc).__name__)
                continue
            report.sent += 1
        if failures:
            raise DigestFailed(
                f"{report.failed} of {report.failed + report.sent} reminder digests not sent "
                f"({', '.join(sorted(set(failures)))}); retrying on the next run"
            )
        return report

    def _recipients(self) -> Iterable[CurrentUser]:
        return [u for u in self._users.list_users() if u.active and u.role is not Role.CLIENT]

    def _items(self, user: CurrentUser, now: datetime, end_of_day: datetime) -> list[FollowUp]:
        items = [
            i for i in self._follow_ups.for_user(user.role.value, user.user_id)
            if i.due_at is not None and i.due_at < end_of_day
        ]  # fmt: skip
        if user.role is Role.ADMINISTRATOR:  # everyone's list: only what is already late
            items = [i for i in items if i.due_at <= now]
        return sorted(items, key=lambda i: i.due_at)

    def render(
        self, user: CurrentUser, items: list[FollowUp], now: datetime, today: date
    ) -> dict[str, str]:
        overdue = [i for i in items if i.due_at <= now]
        due = [i for i in items if i.due_at > now]
        counts = [
            f"{len(overdue)} overdue" if overdue else "",
            f"{len(due)} due today" if due else "",
        ]
        subject = f"Follow-ups for {today:%a %d %b}: " + ", ".join(c for c in counts if c)
        lines = [f"Hello {user.display_name},", ""]
        if user.role is Role.ADMINISTRATOR:
            lines += ["Overdue follow-ups across all staff:", ""]
        shown = 0
        for title, group in (("Overdue", overdue), ("Due today", due)):
            if not group:
                continue
            lines.append(f"{title} ({len(group)})")
            for item in group:
                if shown == MAX_LINES:
                    break
                lines.append(f"- {self._line(item, now)}")
                shown += 1
            lines.append("")
        if len(items) > shown:
            lines += [f"... and {len(items) - shown} more.", ""]
        if self._link:
            lines += [f"Open your follow-up list: {self._link}", ""]
        lines += [
            "Message follow-ups are drafted into the approval queue; nothing is sent to clients"
            " or candidates without approval.",
            "",
            "This is an automatic daily summary from the CBS Recruiting Platform.",
        ]
        return {"subject": subject, "body": "\n".join(lines)}

    def _line(self, item: FollowUp, now: datetime) -> str:
        label = KIND_LABELS.get(item.kind.value, item.kind.value.replace("_", " ").capitalize())
        when = item.due_at.astimezone(self._zone)
        if item.due_at <= now:
            days = (now - item.due_at).days
            timing = f"due {when:%d %b}" + (f", {days} day{'s' * (days != 1)} late" if days else "")
        else:
            timing = f"due {when:%H:%M}"
        return f"{label}: {item.note} ({timing})"
