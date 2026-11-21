"""Opt-out registry, honoured by every agent and channel (compliance checklist E6, E7)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class OptOut:
    address: str
    source: str
    recorded_at: datetime


class OptOutStore(Protocol):
    def get(self, address: str) -> OptOut | None: ...

    def add(self, entry: OptOut) -> OptOut:
        """Store the entry; if the address already exists, keep and return the existing one."""
        ...


class InMemoryOptOutStore:
    def __init__(self) -> None:
        self._entries: dict[str, OptOut] = {}

    def get(self, address: str) -> OptOut | None:
        return self._entries.get(address)

    def add(self, entry: OptOut) -> OptOut:
        return self._entries.setdefault(entry.address, entry)


class OptOutRegistry:
    """Addresses are email addresses or LinkedIn profile URLs. An opt-out blocks all channels."""

    def __init__(
        self,
        store: OptOutStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = InMemoryOptOutStore() if store is None else store
        self._clock = clock

    @staticmethod
    def normalize(address: str) -> str:
        return address.strip().lower().rstrip("/")

    def record(self, address: str, source: str) -> OptOut:
        entry = OptOut(address=self.normalize(address), source=source, recorded_at=self._clock())
        return self._store.add(entry)

    def is_opted_out(self, address: str) -> bool:
        return self._store.get(self.normalize(address)) is not None
