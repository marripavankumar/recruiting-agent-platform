"""Built-in scheduler: follow-ups, delivery retries and career-page re-checks run on time, once
per interval, and the operations view shows when they stop."""

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from recruiting.scheduler import (
    InMemoryJobState,
    Job,
    Scheduler,
    default_jobs,
    status,
)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


def test_each_job_runs_once_per_interval():
    clock, state, calls = Clock(), InMemoryJobState(), []
    jobs = [
        Job("fast", timedelta(minutes=5), lambda: calls.append("fast") or {"sent": 1}),
        Job("slow", timedelta(hours=1), lambda: calls.append("slow")),
    ]
    scheduler = Scheduler(jobs, state, clock)
    assert scheduler.tick() == {"fast": "ok", "slow": "ok"}
    assert scheduler.tick() == {}  # nothing due yet
    clock.now += timedelta(minutes=5)
    assert scheduler.tick() == {"fast": "ok"}
    clock.now += timedelta(minutes=55)
    assert scheduler.tick() == {"fast": "ok", "slow": "ok"}
    assert calls == ["fast", "slow", "fast", "fast", "slow"]
    fast = next(s for s in state.all() if s.name == "fast")
    assert fast.last_status == "ok" and fast.last_result == {"sent": 1}


def test_two_replicas_do_not_run_a_job_twice():
    clock, state, calls = Clock(), InMemoryJobState(), []
    job = Job("follow_ups", timedelta(hours=1), lambda: calls.append(1))
    first, second = Scheduler([job], state, clock), Scheduler([job], state, clock)
    first.tick()
    second.tick()
    assert calls == [1]


def test_a_failing_job_does_not_stop_the_others_and_is_retried_sooner():
    clock, state, calls = Clock(), InMemoryJobState(), []

    def broken():
        calls.append("broken")
        raise RuntimeError("database unreachable")

    jobs = [Job("broken", timedelta(hours=1), broken),
            Job("fine", timedelta(hours=1), lambda: calls.append("fine"))]  # fmt: skip
    scheduler = Scheduler(jobs, state, clock)
    assert scheduler.tick() == {"broken": "failed", "fine": "ok"}
    clock.now += timedelta(minutes=5)  # failed jobs are retried after 5 minutes, not an hour
    assert scheduler.tick() == {"broken": "failed"}
    rows, alerts = status(state, clock.now)
    assert "Scheduled job broken failed: RuntimeError: database unreachable" in alerts


def test_status_alerts_when_jobs_never_ran_or_are_overdue():
    clock, state = Clock(), InMemoryJobState()
    assert status(None, clock.now) == ([], [])  # no scheduler configured
    _, alerts = status(state, clock.now)
    assert len(alerts) == 3 and all("never run" in a for a in alerts)

    jobs = [Job(n, timedelta(hours=1), lambda: None)
            for n in ("follow_ups", "outreach_retry", "hiring_signals")]  # fmt: skip
    Scheduler(jobs, state, clock).tick()
    assert status(state, clock.now)[1] == []
    clock.now += timedelta(hours=2, minutes=1)
    rows, alerts = status(state, clock.now)
    assert len(alerts) == 3 and all("overdue" in a for a in alerts)
    assert all(r["overdue"] for r in rows)


def test_run_forever_stops_when_asked():
    state, stop, calls = InMemoryJobState(), threading.Event(), []
    scheduler = Scheduler([Job("j", timedelta(hours=1), lambda: calls.append(1))], state)
    worker = threading.Thread(target=scheduler.run_forever, args=(stop, 0.01))
    worker.start()
    time.sleep(0.05)
    stop.set()
    worker.join(timeout=2)
    assert not worker.is_alive() and calls == [1]


# ----- the real jobs ----------

pytest.importorskip("langgraph")

from test_phase8_end_to_end import ADMIN, BD, CSV, approve, build, h, ok  # noqa: E402

from recruiting.api.app import create_app  # noqa: E402
from recruiting.outreach import TransientDeliveryError  # noqa: E402


def test_default_jobs_drive_the_platform():
    client, services, email, clock = build()
    real = email.deliver
    refused = [1]

    def deliver(message):
        if refused:
            refused.pop()
            raise TransientDeliveryError("HTTP 429 from the provider")
        return real(message)

    email.deliver = deliver
    ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV}, headers=h(BD)))
    ok(client.post("/companies/hiring-signals", json={}, headers=h(BD)))
    [first] = ok(client.post("/companies/outreach-drafts", json={}, headers=h(BD)))["drafted"]
    approve(client, first["approval_request_id"], BD)
    assert email.sent == []  # waiting to retry

    services.job_state = InMemoryJobState()
    scheduler = Scheduler(default_jobs(services), services.job_state, clock)
    assert [j.name for j in scheduler.jobs] == [
        "follow_ups", "outreach_retry", "hiring_signals", "search_index"
    ]  # fmt: skip
    clock.offset = timedelta(minutes=2)
    assert scheduler.tick() == {
        "follow_ups": "ok", "outreach_retry": "ok", "hiring_signals": "ok", "search_index": "ok"
    }  # fmt: skip
    assert len(email.sent) == 1  # the retry went out
    retry = next(s for s in services.job_state.all() if s.name == "outreach_retry")
    assert retry.last_result["sent"] == 1

    ops = ok(client.get("/admin/operations", headers=h(ADMIN)))
    assert {j["name"] for j in ops["scheduler"]} == {"follow_ups", "outreach_retry",
                                                     "hiring_signals", "search_index"}  # fmt: skip
    assert not any("Scheduled job" in a for a in ops["alerts"])
    clock.offset = timedelta(hours=3)
    ops = ok(client.get("/admin/operations", headers=h(ADMIN)))
    assert "Scheduled job follow_ups is overdue: is the scheduler running?" in ops["alerts"]


def test_api_can_run_the_scheduler_in_process():
    _, services, _, _ = build()
    with TestClient(create_app(services, run_scheduler=True)):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if services.job_state and len(services.job_state.all()) == 3:
                break
            time.sleep(0.02)
        assert {s.last_status for s in services.job_state.all()} == {"ok"}
