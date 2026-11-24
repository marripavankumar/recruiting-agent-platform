"""Retry with back-off for read-only calls to external sources (scenario S24).

Company-data sources (Agent 1) and job boards / resume databases (Agent 6) are searched, never
written to, so a repeated call cannot duplicate anything. Connection errors, timeouts, rate
limits (429) and server errors (5xx) are retried a few times with growing pauses; any other
error, or the last failure, is raised for the caller to record and report to the user.
Sending messages is different and is handled by the Outreach Service.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (httpx.TransportError, TimeoutError, ConnectionError))


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    retry_on: Callable[[Exception], bool] = is_transient,
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not retry_on(exc):
                raise
            sleep(base_delay * 2 ** (attempt - 1))
    raise AssertionError("attempts must be at least 1")
