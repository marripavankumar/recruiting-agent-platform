"""LLM gateway (plan section 5.4): versioned prompt templates, structured output, cost tracking.

Agents never call a model directly. They name a prompt template; the gateway renders it, asks
the active provider for JSON that matches the template's output model, validates it, and records
tokens and cost per template version. Swapping providers (Q4 adapters) does not touch agents.
"""

from __future__ import annotations

import json
import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

Effort = Literal["low", "medium", "high", "xhigh", "max"]
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMRefused(LLMError):
    """The model declined (stop_reason "refusal") even after server-side fallback."""


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: int
    system: str
    user: str  # string.Template syntax: $variable
    output: type[BaseModel]
    effort: Effort = "medium"
    max_tokens: int = 8000

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def render(self, variables: Mapping[str, Any]) -> str:
        return string.Template(self.user).substitute({k: str(v) for k, v in variables.items()})


class PromptRegistry:
    """Keeps every version, so a run can be traced to the exact prompt that produced it."""

    def __init__(self, templates: list[PromptTemplate] | None = None) -> None:
        self._templates: dict[str, dict[int, PromptTemplate]] = {}
        for template in templates or []:
            self.register(template)

    def register(self, template: PromptTemplate) -> None:
        versions = self._templates.setdefault(template.name, {})
        if template.version in versions:
            raise ValueError(f"Prompt {template.key} is already registered")
        versions[template.version] = template

    def get(self, name: str, version: int | None = None) -> PromptTemplate:
        versions = self._templates.get(name)
        if not versions:
            raise KeyError(f"Unknown prompt template {name}")
        return versions[max(versions) if version is None else version]


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for structured output: every object closed and every property required."""

    def close(node: Any) -> Any:
        if isinstance(node, dict):
            # Drop pydantic's "title"/"default" annotations, but never a property named "title".
            node = {
                k: (
                    {name: close(s) for name, s in v.items()}
                    if k in ("properties", "$defs")
                    else close(v)
                )
                for k, v in node.items()
                if k not in ("default", "title")
            }
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            return node
        if isinstance(node, list):
            return [close(v) for v in node]
        return node

    return close(model.model_json_schema())


@dataclass(frozen=True)
class LLMRequest:
    template_key: str
    system: str
    user: str
    json_schema: dict[str, Any]
    effort: Effort
    max_tokens: int


@dataclass(frozen=True)
class LLMResponse:
    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0


class LLMProvider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


# USD per million tokens: (input, output, cache read). First-party rates, 2026-11.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
}


def cost_usd(response: LLMResponse) -> float | None:
    price = PRICES.get(response.model)
    if price is None:
        return None
    uncached = max(response.input_tokens - response.cache_read_tokens, 0)
    return (
        uncached * price[0]
        + response.output_tokens * price[1]
        + response.cache_read_tokens * price[2]
    ) / 1_000_000


@dataclass(frozen=True)
class UsageRecord:
    template_key: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    at: datetime


class UsageLedger(Protocol):
    def record(self, entry: UsageRecord) -> None: ...

    def entries(self) -> list[UsageRecord]: ...


@dataclass
class InMemoryUsageLedger:
    _entries: list[UsageRecord] = field(default_factory=list)

    def record(self, entry: UsageRecord) -> None:
        self._entries.append(entry)

    def entries(self) -> list[UsageRecord]:
        return list(self._entries)


class LLMGateway:
    def __init__(
        self,
        provider: LLMProvider,
        registry: PromptRegistry,
        ledger: UsageLedger | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._ledger = InMemoryUsageLedger() if ledger is None else ledger
        self._clock = clock

    @property
    def ledger(self) -> UsageLedger:
        return self._ledger

    def run(self, template_name: str, output: type[T], **variables: Any) -> T:
        template = self._registry.get(template_name)
        if template.output is not output:
            raise TypeError(f"{template.key} returns {template.output.__name__}")
        request = LLMRequest(
            template_key=template.key,
            system=template.system,
            user=template.render(variables),
            json_schema=strict_schema(template.output),
            effort=template.effort,
            max_tokens=template.max_tokens,
        )
        response = self._provider.complete(request)
        self._ledger.record(
            UsageRecord(
                template_key=template.key,
                provider=self._provider.name,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=cost_usd(response),
                at=self._clock(),
            )
        )
        return output.model_validate(response.data)


class ClaudeProvider:
    """Claude through the official Anthropic SDK, with server-side refusal fallback enabled."""

    name = "claude"

    def __init__(self, client: Any = None, model: str = "claude-opus-5-5") -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()  # credentials from ANTHROPIC_API_KEY or a profile
        self._client = client
        self._model = model

    def complete(self, request: LLMRequest) -> LLMResponse:
        import anthropic

        try:
            response = self._create(request)
        except anthropic.APIStatusError as exc:  # 4xx / 5xx after the SDK's own retries
            raise LLMError(f"{request.template_key}: API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"{request.template_key}: cannot reach the API") from exc
        return self._parse(request, response)

    def _create(self, request: LLMRequest) -> Any:
        return self._client.beta.messages.create(
            model=self._model,
            max_tokens=request.max_tokens,
            system=request.system,
            messages=[{"role": "user", "content": request.user}],
            output_config={
                "effort": request.effort,
                "format": {"type": "json_schema", "schema": request.json_schema},
            },
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    @staticmethod
    def _parse(request: LLMRequest, response: Any) -> LLMResponse:
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise LLMRefused(f"{request.template_key} declined (category {category})")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{request.template_key} hit max_tokens; raise the template limit")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError(f"{request.template_key} returned no text block")
        usage = response.usage
        return LLMResponse(
            data=json.loads(text),
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
        )


class ScriptedProvider:
    """Deterministic provider for tests and local runs without an API key.

    Maps a template name to a function from the rendered request to the JSON answer.
    """

    name = "scripted"

    def __init__(self, scripts: Mapping[str, Callable[[LLMRequest], dict[str, Any]]]) -> None:
        self._scripts = dict(scripts)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        name = request.template_key.split("@", 1)[0]
        if name not in self._scripts:
            raise LLMError(f"No script for {name}")
        return LLMResponse(self._scripts[name](request), "scripted", len(request.user) // 4, 50)
