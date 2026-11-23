"""Signed unsubscribe links and the commercial-email footer (compliance checklist E1-E7).

The token carries the address plus an HMAC, so the public unsubscribe endpoint cannot be used
to opt out someone else's address by guessing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import quote


class UnsubscribeLinks:
    def __init__(self, secret: str, base_url: str) -> None:
        if len(secret) < 16:
            raise ValueError("Unsubscribe secret must be at least 16 characters")
        self._secret = secret.encode()
        self._base_url = base_url.rstrip("/")

    def _sign(self, address: str) -> str:
        digest = hmac.new(self._secret, address.encode(), hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def token(self, address: str) -> str:
        address = address.strip().lower()
        encoded = base64.urlsafe_b64encode(address.encode()).decode().rstrip("=")
        return f"{encoded}.{self._sign(address)}"

    def verify(self, token: str) -> str | None:
        """The address the token was issued for, or None if it is malformed or forged."""
        try:
            encoded, signature = token.split(".", 1)
            address = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        except (ValueError, UnicodeDecodeError):
            return None
        return address if hmac.compare_digest(signature, self._sign(address)) else None

    def url(self, address: str) -> str:
        return f"{self._base_url}/unsubscribe?token={quote(self.token(address))}"


def compliance_footer(sender_name: str, postal_address: str, unsubscribe_url: str) -> str:
    """CAN-SPAM / CASL: sender identity, a valid postal address and a working opt-out."""
    return (
        f"\n\n--\n{sender_name}, CBS LLC\n{postal_address}\n"
        f"To stop receiving emails from CBS LLC, unsubscribe here: {unsubscribe_url} "
        f'or reply "unsubscribe".'
    )
