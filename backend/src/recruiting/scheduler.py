"""Built-in scheduler for the platform's recurring sweeps (plan sections 11 and 12).

Jobs (each one is idempotent, so a late or repeated run does no harm):
- follow_ups: Agent 12 sweep, hourly (Q13 cadence, overdue alerts);
- outreach_retry: deliveries waiting to retry, every 5 minutes (S24);
- hiring_signals: career pages due for a re-check, daily (S4);
- mailbox: replies, JDs and bounce notices from the outreach mailbox, every 2 minutes
  (when RAP_READ_MAILBOX is on);
- with deep agents on (RAP_AGENTS_ENABLED): recruiting_agents every 30 minutes (one run per
  open job that had none for 6 hours), follow_up_agent hourly, business_development_agent
  daily.

Run it as its own process next to the API: `python -m recruiting.scheduler`, with the same
RAP_* settings. Any number of replicas may run: each job run is claimed in the shared
scheduler_job table (PostgreSQL), so a job runs at most once per interval across them. For local
in-memory runs set RAP_RUN_SCHEDULER=true to run it inside the API process instead.
The last run of each job is recorded; the operations view alerts when a job is overdue or failed.
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from recruiting.api.services import Services
    from recruiting.db import Connect

logger = logging.getLogger(__name__)

RETRY_FAILED_AFTER = timedelta(minutes=5)
EXPECTED_JOBS = ("follow_ups", "outreach_retry", "hiring_signals")


@dataclass(frozen=True)
class Job:
    name: str
    interval: timedelta
    run: Callable[[], Any]


@dataclass
class JobState:
    name: str
    interval_seconds: int
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_status: str | None = None  # "running", "ok" or "failed"
    last_error: str | None = None
    last_result: dict[str, Any] = field(default_factory=dict)


class JobStateStore(Protocol):
    def claim(self, name: str, interval: timedelta, now: datetime) -> bool:
        """Atomically mark the job started if it is due; False if not due or taken."""
        ...

    def finish(
        self, name: str, now: datetime, ok: bool, error: str | None, result: dict[str, Any]
    ) -> None: ...

    def all(self) -> list[JobState]: ...


def _due(state: JobState | None, interval: timedelta, now: datetime) -> bool:
    if state is None or state.last_started_at is None:
        return True
    waited = now - state.last_started_at
    return waited >= interval or (state.last_status == "failed" and waited >= RETRY_FAILED_AFTER)


class InMemoryJobState:
    """One process only (local runs, tests)."""

    def __init__(self) -> None:
        self._states: dict[str, JobState] = {}
        self._lock = threading.Lock()

    def claim(self, name: str, interval: timedelta, now: datetime) -> bool:
        with self._lock:
            state = self._states.get(name)
            if not _due(state, interval, now):
                return False
            base = state or JobState(name, int(interval.total_seconds()))
            self._states[name] = replace(
                base,
                interval_seconds=int(interval.total_seconds()),
                last_started_at=now,
                last_status="running",
            )
            return True

    def finish(
        self, name: str, now: datetime, ok: bool, error: str | None, result: dict[str, Any]
    ) -> None:
        with self._lock:
            self._states[name] = replace(
                self._states[name],
                last_finished_at=now,
                last_status="ok" if ok else "failed",
                last_error=error,
                last_result=result,
            )

    def all(self) -> list[JobState]:
        with self._lock:
            return [replace(s) for s in self._states.values()]


class PostgresJobState:
    """scheduler_job table (migration 0011); shared by every scheduler replica."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def claim(self, name: str, interval: timedelta, now: datetime) -> bool:
        seconds = int(interval.total_seconds())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO scheduler_job (name, interval_seconds) VALUES (%s, %s)"
                " ON CONFLICT (name) DO UPDATE SET interval_seconds = EXCLUDED.interval_seconds",
                (name, seconds),
            )
            row = conn.execute(
                """
                UPDATE scheduler_job SET last_started_at = %(now)s, last_status = 'running'
                WHERE name = %(name)s AND (
                    last_started_at IS NULL
                    OR last_started_at <= %(now)s - make_interval(secs => interval_seconds)
                    OR (last_status = 'failed'
                        AND last_started_at <= %(now)s - make_interval(secs => %(retry)s)))
                RETURNING name
                """,
                {"now": now, "name": name, "retry": int(RETRY_FAILED_AFTER.total_seconds())},
            ).fetchone()
        return row is not None

    def finish(
        self, name: str, now: datetime, ok: bool, error: str | None, result: dict[str, Any]
    ) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            conn.execute(
                "UPDATE scheduler_job SET last_finished_at = %s, last_status = %s,"
                " last_error = %s, last_result = %s WHERE name = %s",
                (now, "ok" if ok else "failed", error, Jsonb(result), name),
            )

    def all(self) -> list[JobState]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, interval_seconds, last_started_at, last_finished_at, last_status,"
                " last_error, last_result FROM scheduler_job ORDER BY name"
            ).fetchall()
        return [JobState(*r[:6], dict(r[6] or {})) for r in rows]


def _summary(result: Any) -> dict[str, Any]:
    if is_dataclass(result) and not isinstance(result, type):
        result = asdict(result)
    if not isinstance(result, dict):
        return {}
    # Counts only: keep the stored result small (and free of personal data).
    return {
        k: v for k, v in result.items() if isinstance(v, int | float) and not isinstance(v, bool)
    }


class Scheduler:
    def __init__(
        self,
        jobs: list[Job],
        state: JobStateStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._jobs = jobs
        self._state = state
        self._clock = clock

    @property
    def jobs(self) -> list[Job]:
        return list(self._jobs)

    def tick(self) -> dict[str, str]:
        """Run every job that is due; one failing job does not stop the others."""
        ran: dict[str, str] = {}
        for job in self._jobs:
            if not self._state.claim(job.name, job.interval, self._clock()):
                continue
            try:
                result = _summary(job.run())
            except Exception as exc:
                logger.exception("Scheduled job %s failed", job.name)
                error = f"{type(exc).__name__}: {exc}"[:500]
                self._state.finish(job.name, self._clock(), False, error, {})
                ran[job.name] = "failed"
                continue
            logger.info("Scheduled job %s finished: %s", job.name, result)
            self._state.finish(job.name, self._clock(), True, None, result)
            ran[job.name] = "ok"
        return ran

    def run_forever(self, stop: threading.Event, poll_seconds: float = 30) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:  # e.g. the database is briefly unreachable; try again next poll
                logger.exception("Scheduler tick failed")
            stop.wait(poll_seconds)


def default_jobs(services: Services) -> list[Job]:
    jobs = []
    platform = services.platform
    agents = services.agents
    if platform is not None:
        # With agents on, the follow-up agent drafts the due messages; the sweep only tracks.
        sweep = (lambda: platform.follow_ups.run(auto_draft=False)) if agents else (
            platform.follow_ups.run)  # fmt: skip
        jobs.append(Job("follow_ups", timedelta(hours=1), sweep))
    if services.outreach is not None:
        jobs.append(Job("outreach_retry", timedelta(minutes=5), services.outreach.retry_due))
    if platform is not None:
        jobs.append(Job("hiring_signals", timedelta(days=1), platform.signals.run_due))
    if services.mailbox is not None:
        jobs.append(Job("mailbox", timedelta(minutes=2), services.mailbox.run))
    if agents is not None:
        jobs.append(
            Job("recruiting_agents", timedelta(minutes=30), lambda: _run_recruiting_agents(agents))
        )
        jobs.append(
            Job(
                "follow_up_agent",
                timedelta(hours=1),
                lambda: _agent_report(agents.start("follow_up", None, "scheduler")),
            )
        )
        jobs.append(Job("business_development_agent", timedelta(days=1),
                        lambda: _agent_report(agents.start("business_development", None,
                                                           "scheduler"))))  # fmt: skip
    return jobs


def _agent_report(run: Any) -> dict[str, int]:
    return {"runs": 1, "failed": int(run.status.value == "failed"),
            "waiting": int(run.status.value == "waiting_for_human"),
            "actions": len(run.actions)}  # fmt: skip


def _run_recruiting_agents(agents: Any, limit: int = 5) -> dict[str, int]:
    """Start the recruiting coordinator for open jobs that have had no run for 6 hours."""
    totals = {"runs": 0, "failed": 0, "waiting": 0, "actions": 0}
    for job_id in agents.jobs_to_work(limit=limit):
        for key, value in _agent_report(agents.start("recruiting", job_id, "scheduler")).items():
            totals[key] += value
    return totals


def status(state: JobStateStore | None, now: datetime) -> tuple[list[dict[str, Any]], list[str]]:
    """Each job's last run, and alerts for jobs that never ran, are overdue (twice their
    interval) or failed. `state` is None where no scheduler is configured (tests)."""
    if state is None:
        return [], []
    rows: list[dict[str, Any]] = []
    alerts: list[str] = []
    known = {s.name: s for s in state.all()}
    for name in EXPECTED_JOBS:
        if name not in known or known[name].last_started_at is None:
            alerts.append(f"Scheduled job {name} has never run: is the scheduler running?")
    for s in known.values():
        limit = timedelta(seconds=2 * s.interval_seconds)
        overdue = s.last_started_at is not None and now - s.last_started_at > limit
        if overdue:
            alerts.append(f"Scheduled job {s.name} is overdue: is the scheduler running?")
        if s.last_status == "failed":
            alerts.append(f"Scheduled job {s.name} failed: {s.last_error}")
        rows.append({**asdict(s), "overdue": overdue})
    return rows, alerts


def start_in_background(services: Services, poll_seconds: float = 30) -> threading.Event:
    """Run the scheduler in a daemon thread of the current process; set the event to stop."""
    state = services.job_state or InMemoryJobState()
    services.job_state = state
    stop = threading.Event()
    scheduler = Scheduler(default_jobs(services), state)
    threading.Thread(
        target=scheduler.run_forever, args=(stop, poll_seconds), name="scheduler", daemon=True
    ).start()
    return stop


def main() -> None:
    from recruiting.api.services import build_services
    from recruiting.config import Settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    if settings.storage != "postgres":
        raise SystemExit(
            "The scheduler process needs RAP_STORAGE=postgres (it shares the API's data). "
            "For in-memory runs set RAP_RUN_SCHEDULER=true on the API instead."
        )
    services = build_services(settings)
    scheduler = Scheduler(default_jobs(services), services.job_state or InMemoryJobState())
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logger.info("Scheduler started with jobs: %s", ", ".join(j.name for j in scheduler.jobs))
    scheduler.run_forever(stop)


if __name__ == "__main__":
    main()
