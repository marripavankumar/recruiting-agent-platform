"""PostgreSQL connection factory. Standard PostgreSQL only, so any cloud's managed service works."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

Connect = Callable[[], psycopg.Connection]


def connection_factory(database_url: str) -> Connect:
    """Each call opens a connection; `with connect() as conn:` commits on success.

    A connection pool replaces this once load testing shows it is needed.
    """

    def connect() -> psycopg.Connection:
        return psycopg.connect(database_url)

    return connect


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True
