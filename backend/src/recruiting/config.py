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
    # No fixed default: the Administrator activates one or more per deployment.
    email_providers: list[str] = []  # ["m365"] or ["gmail"]
    email_sender: str | None = None  # mailbox the approved emails are sent from
    document_stores: list[str] = []

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
    # Shared secret the mail-routing workflow (n8n) sends with inbound replies.
    inbound_webhook_secret: str | None = None
    # Vector store: "memory" (in-process) or "qdrant" (at RAP_QDRANT_URL).
    vector_store: Literal["memory", "qdrant"] = "memory"
