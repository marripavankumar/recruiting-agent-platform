"""Builds the application's services from settings: one place where adapters are chosen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from recruiting.approvals import ApprovalEngine
from recruiting.compliance import OptOutRegistry
from recruiting.config import Settings
from recruiting.domain import Channel, Role
from recruiting.identity import (
    Authenticator,
    CurrentUser,
    DevHeaderAuthenticator,
    InMemoryUserDirectory,
    OidcAuthenticator,
    UserDirectory,
)
from recruiting.outreach import (
    ChannelAdapter,
    DeliveryLog,
    GmailEmailChannel,
    ManualLinkedInChannel,
    Microsoft365EmailChannel,
    OutreachService,
)

if TYPE_CHECKING:
    from recruiting.agentic.runs import AgentRunner
    from recruiting.api.platform import Platform
    from recruiting.mailbox import MailboxReader
    from recruiting.scheduler import JobStateStore


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
    outreach: OutreachService | None = None
    # Records, AI services and agents behind /companies, /jobs and /inbound. None: not served.
    platform: Platform | None = None
    # Last run of each scheduled job (operations view). None: no scheduler configured.
    job_state: JobStateStore | None = None
    # Reads the outreach mailbox (scheduled). None: replies arrive only through the webhook.
    mailbox: MailboxReader | None = None
    # Deep agents (RAP_AGENTS_ENABLED). None: the deterministic agents do the work.
    agents: AgentRunner | None = None


def demo_users() -> list[CurrentUser]:
    """One user per staff role for trying the API locally (RAP_DEMO_USERS=true, local only)."""
    return [
        CurrentUser(f"demo-{role.value}", f"{role.value.replace('_', '.')}@cbs.example",
                    f"Demo {role.value.replace('_', ' ').title()}", role)
        for role in Role
        if role is not Role.CLIENT
    ]  # fmt: skip


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
    from recruiting.api.platform import build_platform

    if settings.demo_users and (settings.environment != "local" or settings.storage != "memory"):
        raise RuntimeError("RAP_DEMO_USERS is allowed only for local in-memory runs")
    authenticator = build_authenticator(settings)
    if settings.storage == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        approvals, opt_outs = ApprovalEngine(), OptOutRegistry()
        users = InMemoryUserDirectory(demo_users() if settings.demo_users else ())
        outreach = build_outreach(settings, approvals, opt_outs)
        workflows = build_workflows(approvals, outreach, MemorySaver())
        platform = build_platform(
            settings,
            users=users,
            opt_outs=opt_outs,
            deliveries=outreach.log,
            workflows=workflows,
            approvals=approvals,
        )
        from recruiting.mailbox import InMemoryProcessed
        from recruiting.scheduler import InMemoryJobState

        job_state = InMemoryJobState() if settings.run_scheduler else None
        agents = build_agent_runner(settings, platform, MemorySaver(), None)
        reader = build_mailbox_reader(settings, platform, InMemoryProcessed(), agents)
        return Services(
            approvals, opt_outs, users, authenticator, workflows, outreach, platform, job_state,
            reader, agents,
        )  # fmt: skip

    from recruiting.approvals.postgres import PostgresApprovalStore, PostgresPolicyStore
    from recruiting.compliance.postgres import PostgresOptOutStore
    from recruiting.db import connection_factory, workflow_checkpointer
    from recruiting.identity.postgres import PostgresUserDirectory
    from recruiting.outreach.postgres import PostgresDeliveryLog
    from recruiting.records.postgres import (
        PostgresCadence,
        PostgresCandidates,
        PostgresCompanies,
        PostgresEngagements,
        PostgresFollowUps,
        PostgresJobs,
        PostgresRankings,
        PostgresSubmissions,
        PostgresUsageLedger,
        PostgresWeights,
    )

    connect = connection_factory(settings.database_url)
    approvals = ApprovalEngine(PostgresPolicyStore(connect), store=PostgresApprovalStore(connect))
    opt_outs = OptOutRegistry(PostgresOptOutStore(connect))
    users = PostgresUserDirectory(connect)
    outreach = build_outreach(settings, approvals, opt_outs, PostgresDeliveryLog(connect))
    workflows = build_workflows(approvals, outreach, workflow_checkpointer(settings.database_url))
    platform = build_platform(
        settings,
        users=users,
        opt_outs=opt_outs,
        deliveries=outreach.log,
        workflows=workflows,
        companies=PostgresCompanies(connect),
        jobs=PostgresJobs(connect),
        candidates=PostgresCandidates(connect),
        ledger=PostgresUsageLedger(connect),
        rankings=PostgresRankings(connect),
        ranking_weights=PostgresWeights(connect),
        engagements=PostgresEngagements(connect),
        submissions=PostgresSubmissions(connect),
        follow_ups=PostgresFollowUps(connect),
        follow_up_cadence=PostgresCadence(connect),
        approvals=approvals,
    )
    from recruiting.agentic.runs import PostgresAgentRuns
    from recruiting.mailbox import PostgresProcessed
    from recruiting.scheduler import PostgresJobState

    agents = None
    if settings.agents_enabled:
        agents = build_agent_runner(
            settings, platform, workflow_checkpointer(settings.database_url),
            PostgresAgentRuns(connect),
        )  # fmt: skip
    return Services(
        approvals, opt_outs, users, authenticator, workflows, outreach, platform,
        PostgresJobState(connect),
        build_mailbox_reader(settings, platform, PostgresProcessed(connect), agents),
        agents,
    )  # fmt: skip


def build_agent_runner(settings: Settings, platform: Platform, checkpointer: Any, runs: Any):
    """The deep-agent runner, or None when RAP_AGENTS_ENABLED is off."""
    if not settings.agents_enabled:
        return None
    if settings.llm_provider != "claude":
        raise RuntimeError("RAP_AGENTS_ENABLED needs RAP_LLM_PROVIDER=claude")
    from recruiting.agentic.model import build_chat_model
    from recruiting.agentic.runs import AgentRunner, InMemoryAgentRuns

    return AgentRunner(
        platform,
        build_chat_model(settings.llm_model, settings.agent_effort),
        runs or InMemoryAgentRuns(),
        checkpointer,
        ledger=platform.gateway.ledger if platform.gateway else None,
        model_name=settings.llm_model,
        daily_limit=settings.agent_daily_run_limit,
    )


def build_mailbox_reader(
    settings: Settings, platform: Platform, processed: Any, agents: Any = None
):
    """The reader for the first email provider's mailbox, or None when RAP_READ_MAILBOX is off."""
    if not settings.read_mailbox:
        return None
    from recruiting.connectors.secrets import EnvironmentSecrets
    from recruiting.mailbox import GmailMailbox, MailboxReader, Microsoft365Mailbox

    address = settings.inbound_mailbox or settings.email_sender
    if not settings.email_providers or not address:
        raise RuntimeError(
            "RAP_READ_MAILBOX needs RAP_EMAIL_PROVIDERS and RAP_INBOUND_MAILBOX or RAP_EMAIL_SENDER"
        )
    secrets = EnvironmentSecrets()
    provider = settings.email_providers[0]
    if provider == "m365":
        mailbox: Any = Microsoft365Mailbox(address, lambda: secrets.get("m365_access_token"))
    elif provider == "gmail":
        mailbox = GmailMailbox(lambda: secrets.get("gmail_access_token"))
    else:
        raise RuntimeError(f"Unknown email provider {provider}; use m365 or gmail")
    own = tuple(a for a in (settings.email_sender, address) if a)
    return MailboxReader(mailbox, platform, processed, own_addresses=own, agents=agents)


def build_email_channels(settings: Settings) -> list[ChannelAdapter]:
    """Q11: no fixed default. Tokens come from the secrets adapter on every send, so a rotated
    token is picked up without a restart. The first listed provider is used."""
    from recruiting.compliance.unsubscribe import UnsubscribeLinks
    from recruiting.connectors.secrets import EnvironmentSecrets

    secrets = EnvironmentSecrets()  # cloud vault adapters plug in here (environment plan)
    channels: list[ChannelAdapter] = []
    for provider in settings.email_providers[:1]:
        if not settings.email_sender:
            raise RuntimeError("Set RAP_EMAIL_SENDER to activate an email provider")
        if provider == "m365":
            channels.append(
                Microsoft365EmailChannel(
                    settings.email_sender, lambda: secrets.get("m365_access_token")
                )
            )
        elif provider == "gmail":
            links = UnsubscribeLinks(settings.unsubscribe_secret, settings.public_base_url)
            channels.append(
                GmailEmailChannel(
                    settings.email_sender, lambda: secrets.get("gmail_access_token"), links.url
                )
            )
        else:
            raise RuntimeError(f"Unknown email provider {provider}; use m365 or gmail")
    return channels


def build_outreach(
    settings: Settings,
    approvals: ApprovalEngine,
    opt_outs: OptOutRegistry,
    log: DeliveryLog | None = None,
) -> OutreachService:
    # LinkedIn is manual (Q15); email goes through the active provider, if any.
    from recruiting.outreach.packages import Branding, SubmissionPackageRenderer

    renderer = None
    if settings.submission_attachment != "none":
        branding = Branding(settings.brand_name, settings.brand_color, settings.brand_logo_path)
        renderer = SubmissionPackageRenderer(settings.submission_attachment, branding)
    return OutreachService(
        approvals,
        opt_outs,
        [ManualLinkedInChannel(), *build_email_channels(settings)],
        log=log,
        enabled=settings.outreach_enabled,
        allow_list=settings.outreach_allow_list,
        daily_caps={Channel.EMAIL: settings.outreach_daily_cap_email},
        attachments=renderer,
    )


def build_workflows(approvals: ApprovalEngine, outreach: OutreachService, checkpointer: Any):
    from recruiting.workflows.outreach_graph import OutreachWorkflow, build_outreach_graph

    return OutreachWorkflow(build_outreach_graph(approvals, outreach, checkpointer))
