"""Connector framework (plan section 5.5) and cloud plug-and-play (environment plan section 1).

Every integration, business or cloud, is registered as (type, name) -> factory. Configuration
decides which adapters are active, so adding a provider or moving clouds means registering a
new adapter, not changing agent code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any


class ConnectorType(StrEnum):
    # Business integrations (BRD section 11)
    COMPANY_DATA = "company_data"
    JOB_SIGNALS = "job_signals"
    CANDIDATE_SOURCE = "candidate_source"
    EMAIL = "email"
    CRM_ATS = "crm_ats"
    RESUME_PARSER = "resume_parser"
    DOCUMENT_STORE = "document_store"
    VECTOR_DB = "vector_db"
    LLM = "llm"
    WORKFLOW = "workflow"
    # Cloud services (any cloud, plug-and-play)
    SECRETS = "secrets"
    OBJECT_STORAGE = "object_storage"


Factory = Callable[[Mapping[str, str]], Any]


class UnknownConnector(KeyError):
    pass


class ConnectorRegistry:
    def __init__(self) -> None:
        self._factories: dict[tuple[ConnectorType, str], Factory] = {}
        self._active: dict[ConnectorType, dict[str, Any]] = {}

    def register(self, connector_type: ConnectorType, name: str, factory: Factory) -> None:
        key = (connector_type, name)
        if key in self._factories:
            raise ValueError(f"Connector {connector_type}/{name} is already registered")
        self._factories[key] = factory

    def available(self, connector_type: ConnectorType) -> list[str]:
        return sorted(name for (t, name) in self._factories if t is connector_type)

    def activate(
        self, connector_type: ConnectorType, name: str, settings: Mapping[str, str] | None = None
    ) -> Any:
        factory = self._factories.get((connector_type, name))
        if factory is None:
            raise UnknownConnector(f"{connector_type}/{name}")
        instance = factory(settings or {})
        self._active.setdefault(connector_type, {})[name] = instance
        return instance

    def deactivate(self, connector_type: ConnectorType, name: str) -> None:
        self._active.get(connector_type, {}).pop(name, None)

    def active(self, connector_type: ConnectorType) -> dict[str, Any]:
        return dict(self._active.get(connector_type, {}))
