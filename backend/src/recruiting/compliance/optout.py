"""Opt-out registry, honoured by every agent and channel (compliance checklist E6, E7)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class OptOut:
    address: str
    source: str
    recorded_at: datetime


class OptOutRegistry:
    """Addresses are email addresses or LinkedIn profile URLs. An opt-out blocks all channels."""

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._entries: dict[str, OptOut] = {}
        self._clock = clock

    @staticmethod
    def normalize(address: str) -> str:
        return address.strip().lower().rstrip("/")

    def record(self, address: str, source: str) -> OptOut:
        key = self.normalize(address)
        existing = self._entries.get(key)
        if existing is not None:
            return existing
        entry = OptOut(address=key, source=source, recorded_at=self._clock())
        self._entries[key] = entry
        return entry

    def is_opted_out(self, address: str) -> bool:
        return self.normalize(address) in self._entries
