"""Authentication adapters. They return a verified email address; roles come from app_user.

- OidcAuthenticator: any OpenID Connect provider (Microsoft Entra ID, Amazon Cognito, Google
  Identity Platform, Keycloak, Okta, ...). This is what keeps sign-in cloud-agnostic.
- DevHeaderAuthenticator: trusts an X-Dev-User-Email header. Local development only; the app
  refuses to start with it in any other environment.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import jwt


class AuthenticationError(Exception):
    pass


class Authenticator(Protocol):
    def identify(self, headers: Mapping[str, str]) -> str: ...


class DevHeaderAuthenticator:
    header = "x-dev-user-email"

    def identify(self, headers: Mapping[str, str]) -> str:
        email = headers.get(self.header)
        if not email:
            raise AuthenticationError(f"Missing {self.header} header")
        return email


KeyResolver = Callable[[str], Any]


class OidcAuthenticator:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        email_claim: str = "email",
        algorithms: Sequence[str] = ("RS256", "ES256"),
        key_resolver: KeyResolver | None = None,
    ) -> None:
        if key_resolver is None:
            if not jwks_url:
                raise ValueError("OIDC needs a JWKS URL")
            jwks = jwt.PyJWKClient(jwks_url)
            key_resolver = lambda token: jwks.get_signing_key_from_jwt(token).key  # noqa: E731
        self._issuer = issuer
        self._audience = audience
        self._email_claim = email_claim
        self._algorithms = list(algorithms)
        self._resolve_key = key_resolver

    def identify(self, headers: Mapping[str, str]) -> str:
        authorization = headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationError("Missing bearer token")
        try:
            claims = jwt.decode(
                token,
                self._resolve_key(token),
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"Invalid token: {exc}") from exc
        email = claims.get(self._email_claim)
        if not isinstance(email, str) or not email:
            raise AuthenticationError(f"Token has no {self._email_claim} claim")
        # Roles are looked up by email, so an address the provider says it has not verified
        # must not sign anyone in. (Some providers send the flag as a string.)
        if (
            self._email_claim == "email"
            and str(claims.get("email_verified", True)).lower() == "false"
        ):
            raise AuthenticationError("The email address is not verified by the sign-in provider")
        return email
