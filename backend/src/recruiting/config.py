"""Runtime settings. Everything that varies by environment or cloud comes from RAP_* variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAP_", env_file=".env", extra="ignore")

    environment: str = "local"
    database_url: str = "postgresql://rap:rap@localhost:5432/rap"
    qdrant_url: str = "http://localhost:6333"

    # Cloud plug-and-play: which adapter backs each cloud service.
    secrets_provider: str = "environment"

    # Default active adapters (stack decision record, 2026-11-22).
    llm_provider: str = "claude"
    resume_parser: str = "affinda"
    # No fixed default: the Administrator activates one or more per deployment.
    email_providers: list[str] = []
    document_stores: list[str] = []

    # Outreach is off outside prod unless explicitly allowed (environment plan section 2).
    outreach_enabled: bool = False
