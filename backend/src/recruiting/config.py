"""Runtime settings. Everything that varies by environment or cloud comes from RAP_* variables."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Repo-root .env, then backend/.env; later files win.
    model_config = SettingsConfigDict(
        env_prefix="RAP_", env_file=("../.env", ".env"), extra="ignore"
    )

    environment: Literal["local", "dev", "test", "prod"] = "local"
    database_url: str = "postgresql://rap:rap@localhost:5432/rap"
    qdrant_url: str = "http://localhost:6333"

    # "memory" keeps everything in-process (tests, quick local runs); "postgres" persists.
    storage: Literal["memory", "postgres"] = "memory"
    # Local in-memory runs only: sign in as administrator@cbs.example, bd.manager@cbs.example,
    # recruiter@cbs.example, account.manager@cbs.example or sales.manager@cbs.example.
    demo_users: bool = False
    # Local in-memory runs only: also load a fictional pipeline (companies, jobs, candidates,
    # approvals waiting) to click through in the web app. Emails go to an in-memory outbox.
    demo_data: bool = False

    # Sign-in: "dev" is local-only; "oidc" works with any OpenID Connect provider.
    auth_provider: Literal["dev", "oidc"] = "dev"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_email_claim: str = "email"

    # Cloud plug-and-play: which adapter backs each cloud service.
    secrets_provider: str = "environment"

    # Default active adapters (stack decision record, 2026-11-22).
    # "claude" uses the Anthropic API (ANTHROPIC_API_KEY or an `ant auth login` profile);
    # "none" runs every agent on its rule-based fallback.
    llm_provider: Literal["claude", "none"] = "claude"
    llm_model: str = "claude-opus-5-5"
    resume_parser: str = "affinda"
    # Affinda (resume_parser=affinda): used only when a workspace is set and the API key is in the
    # secrets provider (RAP_SECRET_AFFINDA_API_KEY); otherwise Claude and the rules parse resumes.
    # The key works only on its region: https://api.us1.affinda.com (US), api.eu1 (EU),
    # api.affinda.com (global/AU).
    affinda_workspace: str | None = None
    affinda_document_type: str | None = None
    affinda_base_url: str = "https://api.us1.affinda.com"
    # No fixed default: the Administrator activates one or more per deployment.
    email_providers: list[str] = []  # ["m365"] or ["gmail"]
    email_sender: str | None = None  # mailbox the approved emails are sent from
    # Where original resume files are kept: ["s3"] (AWS S3, MinIO or any S3-compatible store).
    # Empty: in-memory runs keep files in memory; PostgreSQL runs keep only the resume text.
    document_stores: list[str] = []
    s3_bucket: str | None = None  # default: rap-<environment>-documents, one per environment
    s3_endpoint_url: str | None = None  # MinIO or another S3-compatible store; None for AWS
    s3_region: str | None = None

    # Outreach is off outside prod unless explicitly allowed (environment plan section 2).
    outreach_enabled: bool = False
    # Test environment: when set, outreach goes only to these addresses (JSON list).
    outreach_allow_list: list[str] = []

    # Commercial email compliance (checklist E1-E7) and Agent 3.
    public_base_url: str = "http://localhost:8000"
    unsubscribe_secret: str = "local-dev-unsubscribe-secret"  # set a real secret outside local
    outreach_sender_name: str = "CBS LLC Business Development"
    outreach_postal_address: str = "CBS LLC, postal address to be configured"
    outreach_daily_cap_email: int = 100  # BRD target 200-500 a week after approval
    client_outreach_min_priority: int = 70
    # LinkedIn caps connection invitations per week; drafts stop at this number (Agent 8).
    linkedin_weekly_limit: int = 100
    # Shared secret the mail-routing workflow (n8n) sends with inbound replies.
    inbound_webhook_secret: str | None = None
    # Agent 11: the client package is attached to the submission email as a branded document.
    submission_attachment: Literal["pdf", "docx", "none"] = "pdf"
    brand_name: str = "CBS LLC"
    brand_color: str = "#1F3A93"
    brand_logo_path: str | None = None  # PNG or JPEG
    # Deep agents (LangGraph + deepagents) drive the workflow when on; needs llm_provider=claude.
    # Off: every agent runs its deterministic code path, as before.
    agents_enabled: bool = False
    agent_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    agent_daily_run_limit: int = 200
    # Read replies, JDs and bounce notices from the outreach mailbox (the first email provider)
    # every 2 minutes. Needs gmail.modify (Gmail) or Mail.ReadWrite (Microsoft 365).
    read_mailbox: bool = False
    inbound_mailbox: str | None = None  # defaults to RAP_EMAIL_SENDER
    # Run the scheduled sweeps inside the API process (local in-memory runs). Deployments run
    # `python -m recruiting.scheduler` as its own process instead.
    run_scheduler: bool = False
    # Daily email to each staff user with their overdue and due-today follow-ups (Agent 12).
    # Sent through the first email provider; in demo runs it goes to the in-memory outbox.
    reminder_digest: bool = False
    digest_hour: int = 7  # local hour the digest goes out
    digest_timezone: str = "America/New_York"
    digest_weekends: bool = False
    web_base_url: str | None = None  # the web app, for the link in the digest
    # Vector store: "memory" (in-process) or "qdrant" (at RAP_QDRANT_URL).
    vector_store: Literal["memory", "qdrant"] = "memory"
