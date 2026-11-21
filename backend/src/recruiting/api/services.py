"""Builds the application's services from settings: one place where adapters are chosen."""

from __future__ import annotations

from dataclasses import dataclass

from recruiting.approvals import ApprovalEngine
from recruiting.compliance import OptOutRegistry
from recruiting.config import Settings
from recruiting.identity import (
    Authenticator,
    DevHeaderAuthenticator,
    InMemoryUserDirectory,
    OidcAuthenticator,
    UserDirectory,
)


@dataclass
class Services:
    approvals: ApprovalEngine
    opt_outs: OptOutRegistry
    users: UserDirectory
    authenticator: Authenticator


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.auth_provider == "dev":
        if settings.environment != "local":
            raise RuntimeError("Dev header sign-in is allowed only when RAP_ENVIRONMENT=local")
        return DevHeaderAuthenticator()
    if not (settings.oidc_issuer and settings.oidc_audience and settings.oidc_jwks_url):
        raise RuntimeError("OIDC needs RAP_OIDC_ISSUER, RAP_OIDC_AUDIENCE and RAP_OIDC_JWKS_URL")
    return OidcAuthenticator(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks_url=settings.oidc_jwks_url,
        email_claim=settings.oidc_email_claim,
    )


def build_services(settings: Settings) -> Services:
    authenticator = build_authenticator(settings)
    if settings.storage == "memory":
        return Services(ApprovalEngine(), OptOutRegistry(), InMemoryUserDirectory(), authenticator)

    from recruiting.approvals.postgres import PostgresApprovalStore, load_gate_policies
    from recruiting.compliance.postgres import PostgresOptOutStore
    from recruiting.db import connection_factory
    from recruiting.identity.users import PostgresUserDirectory

    connect = connection_factory(settings.database_url)
    return Services(
        approvals=ApprovalEngine(load_gate_policies(connect), store=PostgresApprovalStore(connect)),
        opt_outs=OptOutRegistry(PostgresOptOutStore(connect)),
        users=PostgresUserDirectory(connect),
        authenticator=authenticator,
    )
