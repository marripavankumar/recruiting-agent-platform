"""Platform users and their roles (BRD section 9).

The identity provider only proves who someone is. Their role comes from the platform's own
app_user table, managed by the Administrator, so roles work the same on every cloud.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from recruiting.db import Connect
from recruiting.domain import Role


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    email: str
    display_name: str
    role: Role
    client_company_id: str | None = None  # set only for Client users (Q8)


class UserDirectory(Protocol):
    def by_email(self, email: str) -> CurrentUser | None: ...


class InMemoryUserDirectory:
    def __init__(self, users: Iterable[CurrentUser] = ()) -> None:
        self._users = {u.email.lower(): u for u in users}

    def by_email(self, email: str) -> CurrentUser | None:
        return self._users.get(email.strip().lower())


class PostgresUserDirectory:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def by_email(self, email: str) -> CurrentUser | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id::text, email, display_name, role, client_company_id::text
                FROM app_user WHERE lower(email) = lower(%s) AND active
                """,
                (email.strip(),),
            ).fetchone()
        if row is None:
            return None
        user_id, user_email, display_name, role, client_company_id = row
        return CurrentUser(user_id, user_email, display_name, Role(role), client_company_id)
