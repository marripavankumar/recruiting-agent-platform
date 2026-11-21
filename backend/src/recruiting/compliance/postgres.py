"""PostgreSQL persistence for the opt-out registry (opt_out table, migration 0001)."""

from __future__ import annotations

from recruiting.compliance.optout import OptOut
from recruiting.db import Connect


class PostgresOptOutStore:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def get(self, address: str) -> OptOut | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT address, source, recorded_at FROM opt_out WHERE address = %s", (address,)
            ).fetchone()
        return OptOut(*row) if row else None

    def add(self, entry: OptOut) -> OptOut:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO opt_out (address, source, recorded_at) VALUES (%s, %s, %s)
                ON CONFLICT (address) DO NOTHING
                """,
                (entry.address, entry.source, entry.recorded_at),
            )
            row = conn.execute(
                "SELECT address, source, recorded_at FROM opt_out WHERE address = %s",
                (entry.address,),
            ).fetchone()
        return OptOut(*row)
