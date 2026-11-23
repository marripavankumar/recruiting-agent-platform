"""Platform users and their roles (BRD section 9).

The identity provider only proves who someone is. Their role comes from the platform's own
app_user table, managed by the Administrator, so roles work the same on every cloud.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Protocol

from recruiting.domain import Role


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    email: str
    display_name: str
    role: Role
    client_company_id: str | None = None  # set only for Client users (Q8)
    active: bool = True


class UserError(Exception):
    pass


class DuplicateUser(UserError):
    pass


class UnknownUser(UserError):
    pass


class InvalidUser(UserError):
    pass


def validate_user(role: Role, client_company_id: str | None) -> None:
    """Client users belong to exactly one client company; staff users to none (Q8)."""
    if (role is Role.CLIENT) != (client_company_id is not None):
        raise InvalidUser("Client users need a client company; staff users must not have one")


class UserDirectory(Protocol):
    def by_email(self, email: str) -> CurrentUser | None:
        """The active user with this email, or None."""
        ...

    def list_users(self) -> list[CurrentUser]: ...

    def create_user(
        self,
        email: str,
        display_name: str,
        role: Role,
        client_company_id: str | None,
        created_by: str,
    ) -> CurrentUser: ...

    def update_user(
        self,
        user_id: str,
        updated_by: str,
        *,
        display_name: str | None = None,
        role: Role | None = None,
        active: bool | None = None,
    ) -> CurrentUser: ...


class InMemoryUserDirectory:
    def __init__(self, users: Iterable[CurrentUser] = ()) -> None:
        self._users = {u.user_id: u for u in users}

    def by_email(self, email: str) -> CurrentUser | None:
        wanted = email.strip().lower()
        return next(
            (u for u in self._users.values() if u.email.lower() == wanted and u.active), None
        )

    def list_users(self) -> list[CurrentUser]:
        return sorted(self._users.values(), key=lambda u: u.email.lower())

    def create_user(
        self,
        email: str,
        display_name: str,
        role: Role,
        client_company_id: str | None,
        created_by: str,
    ) -> CurrentUser:
        validate_user(role, client_company_id)
        email = email.strip()
        if any(u.email.lower() == email.lower() for u in self._users.values()):
            raise DuplicateUser(f"A user with email {email} already exists")
        user = CurrentUser(str(uuid.uuid4()), email, display_name, role, client_company_id)
        self._users[user.user_id] = user
        return user

    def update_user(
        self,
        user_id: str,
        updated_by: str,
        *,
        display_name: str | None = None,
        role: Role | None = None,
        active: bool | None = None,
    ) -> CurrentUser:
        user = self._users.get(user_id)
        if user is None:
            raise UnknownUser(f"Unknown user {user_id}")
        changes = {"display_name": display_name, "role": role, "active": active}
        user = replace(user, **{k: v for k, v in changes.items() if v is not None})
        validate_user(user.role, user.client_company_id)
        self._users[user_id] = user
        return user
