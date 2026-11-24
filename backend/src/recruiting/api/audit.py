"""Access log for refused requests (scenario S26: access denied and logged).

Every 401 and 403 is written to the "recruiting.audit" logger, which production ships to the
central log store, and the most recent ones are kept in memory for the Administrator's
operations view. Request bodies and tokens are never logged.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import Request

logger = logging.getLogger("recruiting.audit")


class AccessLog:
    def __init__(self, keep: int = 500) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=keep)

    def record(self, request: Request, status_code: int) -> dict[str, Any]:
        user = getattr(request.state, "user", None)
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "status": status_code,
            "method": request.method,
            "path": request.url.path,
            "user_id": user.user_id if user else None,
            "role": user.role.value if user else None,
            "client_ip": request.client.host if request.client else None,
        }
        self._entries.append(entry)
        logger.warning("access_denied %s", entry)
        return entry

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self._entries)[-limit:][::-1]
