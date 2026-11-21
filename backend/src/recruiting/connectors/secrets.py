"""Secrets provider interface. One adapter per cloud vault, chosen by RAP_SECRETS_PROVIDER.

Planned adapters: azure_key_vault, aws_secrets_manager, gcp_secret_manager, hashicorp_vault.
Only the environment adapter ships in the scaffold; it is for local and dev use.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol

from recruiting.connectors.registry import ConnectorRegistry, ConnectorType


class SecretsProvider(Protocol):
    def get(self, name: str) -> str: ...


class EnvironmentSecrets:
    """Reads secrets from environment variables (prefix RAP_SECRET_). Local and dev only."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def get(self, name: str) -> str:
        key = f"RAP_SECRET_{name.upper()}"
        try:
            return self._environ[key]
        except KeyError:
            raise KeyError(f"Secret {name} not set ({key})") from None


def register_builtin_secrets(registry: ConnectorRegistry) -> None:
    registry.register(ConnectorType.SECRETS, "environment", lambda _settings: EnvironmentSecrets())
