"""Builds the application's services from settings: one place where adapters are chosen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

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
from recruiting.outreach import DeliveryLog, ManualLinkedInChannel, OutreachService


class WorkflowResumer(Protocol):
    def resume(self, approval_request_id: str) -> Any: ...


@dataclass
class Services:
    approvals: ApprovalEngine
    opt_outs: OptOutRegistry
    users: UserDirectory
    authenticator: Authenticator
    # Resumes the workflow thread waiting on an approval once it is decided. None: no workflows.
    workflows: WorkflowResumer | None = None


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
        from langgraph.checkpoint.memory import MemorySaver

        approvals, opt_outs = ApprovalEngine(), OptOutRegistry()
        outreach = build_outreach(settings, approvals, opt_outs)
        workflows = build_workflows(approvals, outreach, MemorySaver())
        return Services(approvals, opt_outs, InMemoryUserDirectory(), authenticator, workflows)

    from recruiting.approvals.postgres import PostgresApprovalStore, PostgresPolicyStore
    from recruiting.compliance.postgres import PostgresOptOutStore
    from recruiting.db import connection_factory, workflow_checkpointer
    from recruiting.identity.postgres import PostgresUserDirectory
    from recruiting.outreach.postgres import PostgresDeliveryLog

    connect = connection_factory(settings.database_url)
    approvals = ApprovalEngine(PostgresPolicyStore(connect), store=PostgresApprovalStore(connect))
    opt_outs = OptOutRegistry(PostgresOptOutStore(connect))
    outreach = build_outreach(settings, approvals, opt_outs, PostgresDeliveryLog(connect))
    return Services(
        approvals=approvals,
        opt_outs=opt_outs,
        users=PostgresUserDirectory(connect),
        authenticator=authenticator,
        workflows=build_workflows(
            approvals, outreach, workflow_checkpointer(settings.database_url)
        ),
    )


def build_outreach(
    settings: Settings,
    approvals: ApprovalEngine,
    opt_outs: OptOutRegistry,
    log: DeliveryLog | None = None,
) -> OutreachService:
    # Email adapters (Microsoft 365, Gmail) arrive in Phase 2; LinkedIn is manual (Q15).
    return OutreachService(
        approvals,
        opt_outs,
        [ManualLinkedInChannel()],
        log=log,
        enabled=settings.outreach_enabled,
        allow_list=settings.outreach_allow_list,
    )


def build_workflows(approvals: ApprovalEngine, outreach: OutreachService, checkpointer: Any):
    from recruiting.workflows.outreach_graph import OutreachWorkflow, build_outreach_graph

    return OutreachWorkflow(build_outreach_graph(approvals, outreach, checkpointer))
