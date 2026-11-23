"""PostgreSQL user directory (app_user table); imported only when RAP_STORAGE=postgres."""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from recruiting.db import Connect, is_uuid
from recruiting.domain import Role
from recruiting.identity.users import (
    CurrentUser,
    DuplicateUser,
    InvalidUser,
    UnknownUser,
    validate_user,
)

_COLUMNS = "id::text, email, display_name, role, client_company_id::text, active"


def _user(row: tuple) -> CurrentUser:
    user_id, email, display_name, role, client_company_id, active = row
    return CurrentUser(user_id, email, display_name, Role(role), client_company_id, active)


def _audit(conn: psycopg.Connection, actor: str, action: str, user_id: str, details: dict) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (actor, action, entity_type, entity_id, details)
        VALUES (%s, %s, 'app_user', %s, %s)
        """,
        (actor, action, user_id, Jsonb(details)),
    )


class PostgresUserDirectory:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    def by_email(self, email: str) -> CurrentUser | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM app_user WHERE lower(email) = lower(%s) AND active",
                (email.strip(),),
            ).fetchone()
        return _user(row) if row else None

    def list_users(self) -> list[CurrentUser]:
        with self._connect() as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM app_user ORDER BY lower(email)").fetchall()
        return [_user(r) for r in rows]

    def create_user(
        self,
        email: str,
        display_name: str,
        role: Role,
        client_company_id: str | None,
        created_by: str,
    ) -> CurrentUser:
        validate_user(role, client_company_id)
        if client_company_id is not None and not is_uuid(client_company_id):
            raise InvalidUser("Unknown client company")
        email = email.strip()
        try:
            with self._connect() as conn:
                taken = conn.execute(
                    "SELECT 1 FROM app_user WHERE lower(email) = lower(%s)", (email,)
                ).fetchone()
                if taken:
                    raise DuplicateUser(f"A user with email {email} already exists")
                row = conn.execute(
                    f"""
                    INSERT INTO app_user (email, display_name, role, client_company_id)
                    VALUES (%s, %s, %s, %s) RETURNING {_COLUMNS}
                    """,
                    (email, display_name, role.value, client_company_id),
                ).fetchone()
                user = _user(row)
                _audit(conn, created_by, "user.create", user.user_id, {"role": role.value})
        except psycopg.errors.UniqueViolation as exc:  # lost a race with an identical create
            raise DuplicateUser(f"A user with email {email} already exists") from exc
        except psycopg.errors.ForeignKeyViolation as exc:
            raise InvalidUser("Unknown client company") from exc
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
        if not is_uuid(user_id):
            raise UnknownUser(f"Unknown user {user_id}")
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM app_user WHERE id = %s FOR UPDATE", (user_id,)
            ).fetchone()
            if row is None:
                raise UnknownUser(f"Unknown user {user_id}")
            current = _user(row)
            new_role = role or current.role
            validate_user(new_role, current.client_company_id)
            row = conn.execute(
                f"""
                UPDATE app_user SET display_name = %s, role = %s, active = %s
                WHERE id = %s RETURNING {_COLUMNS}
                """,
                (
                    current.display_name if display_name is None else display_name,
                    new_role.value,
                    current.active if active is None else active,
                    user_id,
                ),
            ).fetchone()
            changes = {
                "display_name": display_name,
                "role": role.value if role else None,
                "active": active,
            }
            _audit(
                conn,
                updated_by,
                "user.update",
                user_id,
                {k: v for k, v in changes.items() if v is not None},
            )
        return _user(row)
