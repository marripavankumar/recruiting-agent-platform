"""OpenRouter: one API key for many models (Claude, GPT, Gemini, Llama, Qwen, ...).

RAP_LLM_PROVIDER=openrouter. The key is read from the secrets provider on every call
(RAP_SECRET_OPENROUTER_API_KEY), so a rotated key is picked up without a restart.

- `OpenRouterCatalog` lists the models the Administrator can choose from, live from
  GET /api/v1/models (no key needed), cached for an hour. Only models that support both tool
  calls (the deep agents) and structured outputs (the LLM gateway's JSON answers) are listed.
- Each model is put in a tier by its price: Free, Basic, or Premium (input at or above
  PREMIUM_INPUT_PER_MILLION dollars per million tokens).
- `ModelSelection` keeps the Administrator's choice (memory or PostgreSQL, migration 0016);
  the gateway and the deep agents read it on every call, so a change applies at once.
- `OpenRouterProvider` is the LLM gateway adapter: chat completions with a strict JSON schema,
  routed only to providers that support every parameter sent (provider.require_parameters).

OpenRouter is a data processor: prompts (resume text, emails, JDs) pass through it on the way
to the model provider (compliance checklist, section on processors).
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

import httpx

from recruiting.ai.llm import LLMError, LLMRefused, LLMRequest, LLMResponse

OPENROUTER_URL = "https://openrouter.ai/api/v1"
# Both are needed: tool calls for the deep agents, structured outputs for the gateway.
REQUIRED_PARAMETERS = ("tools", "structured_outputs")
# Dollars per million input tokens at or above which a model is Premium.
PREMIUM_INPUT_PER_MILLION = 1.0


class ModelTier(StrEnum):
    PREMIUM = "premium"
    BASIC = "basic"
    FREE = "free"


TIER_ORDER = {ModelTier.PREMIUM: 0, ModelTier.BASIC: 1, ModelTier.FREE: 2}


class CatalogUnavailable(LLMError):
    """The model list could not be read from OpenRouter and no earlier copy is cached."""


class UnknownModel(ValueError):
    pass


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    tier: ModelTier
    input_per_million: float  # USD
    output_per_million: float  # USD
    context_length: int | None = None
    reasoning: bool = False
    efforts: tuple[str, ...] = ()  # reasoning efforts the model accepts, if listed


def tier_for(input_per_million: float, output_per_million: float) -> ModelTier:
    if input_per_million == 0 and output_per_million == 0:
        return ModelTier.FREE
    if input_per_million >= PREMIUM_INPUT_PER_MILLION:
        return ModelTier.PREMIUM
    return ModelTier.BASIC


def _per_million(value: Any) -> float | None:
    """OpenRouter prices are strings in USD per token; "-1" marks a router with no fixed price."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return None if price < 0 else round(price * 1_000_000, 6)


def parse_model(raw: dict[str, Any]) -> CatalogModel | None:
    """One entry of GET /models, or None when it cannot be offered (no fixed price, or it lacks
    tool calls or structured outputs)."""
    model_id = raw.get("id")
    pricing = raw.get("pricing") or {}
    prices = _per_million(pricing.get("prompt")), _per_million(pricing.get("completion"))
    if not model_id or prices[0] is None or prices[1] is None:
        return None
    supported = raw.get("supported_parameters")
    if supported is not None and not all(p in supported for p in REQUIRED_PARAMETERS):
        return None
    reasoning_info = raw.get("reasoning") if isinstance(raw.get("reasoning"), dict) else {}
    context = raw.get("context_length")
    return CatalogModel(
        id=model_id,
        name=raw.get("name") or model_id,
        tier=tier_for(prices[0], prices[1]),
        input_per_million=prices[0],
        output_per_million=prices[1],
        context_length=context if isinstance(context, int) else None,
        reasoning="reasoning" in (supported or ()),
        efforts=tuple(reasoning_info.get("supported_efforts") or ()),
    )


class OpenRouterCatalog:
    """The models on offer, cached for `ttl`. If a refresh fails the last good list is kept."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        ttl: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._http = client or httpx.Client(base_url=OPENROUTER_URL, timeout=30)
        self._ttl, self._clock = ttl, clock
        self._models: list[CatalogModel] | None = None
        self._loaded_at: datetime | None = None
        self._lock = threading.Lock()

    def models(self) -> list[CatalogModel]:
        with self._lock:
            now = self._clock()
            if self._models is not None and self._loaded_at and now - self._loaded_at < self._ttl:
                return list(self._models)
            try:
                self._models = self._fetch()
                self._loaded_at = now
            except (httpx.HTTPError, ValueError) as exc:
                if self._models is None:
                    message = f"Cannot read the OpenRouter model list: {exc}"
                    raise CatalogUnavailable(message) from exc
            return list(self._models)

    def get(self, model_id: str) -> CatalogModel | None:
        return next((m for m in self.models() if m.id == model_id), None)

    def _fetch(self) -> list[CatalogModel]:
        response = self._http.get(
            "/models", params={"supported_parameters": ",".join(REQUIRED_PARAMETERS)}
        )
        response.raise_for_status()
        entries = response.json().get("data")
        if not isinstance(entries, list):
            raise ValueError("unexpected response: no data list")
        models = [m for m in (parse_model(e) for e in entries if isinstance(e, dict)) if m]
        return sorted(models, key=lambda m: (TIER_ORDER[m.tier], m.name.lower()))


class ModelChoiceStore(Protocol):
    def get(self) -> str | None: ...

    def save(self, model_id: str, by: str) -> None: ...


class InMemoryModelChoice:
    def __init__(self) -> None:
        self._model: str | None = None

    def get(self) -> str | None:
        return self._model

    def save(self, model_id: str, by: str) -> None:
        self._model = model_id


@dataclass
class ModelSelection:
    """The model the platform uses: the Administrator's choice, else RAP_OPENROUTER_MODEL."""

    catalog: OpenRouterCatalog
    store: ModelChoiceStore
    default: str | None = None

    def current(self) -> str | None:
        return self.store.get() or self.default

    def choose(self, model_id: str, by: str) -> CatalogModel:
        model = self.catalog.get(model_id)  # CatalogUnavailable if OpenRouter cannot be read
        if model is None:
            raise UnknownModel(
                f"{model_id} is not offered: it is unknown, has no fixed price, "
                "or lacks tool calls or structured outputs"
            )
        self.store.save(model_id, by)
        return model

    def reasoning_effort(self, model_id: str, wanted: str) -> str | None:
        """The effort to send, or None when the model takes no reasoning setting. A model that
        lists its efforts gets the wanted one if it accepts it, else its nearest lower one."""
        try:
            model = self.catalog.get(model_id)
        except CatalogUnavailable:
            return None
        if model is None or not model.reasoning:
            return None
        if not model.efforts or wanted in model.efforts:
            return wanted
        ladder = ("max", "xhigh", "high", "medium", "low", "minimal")
        lower = ladder[ladder.index(wanted) :] if wanted in ladder else ladder
        return next((e for e in lower if e in model.efforts), None)


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _schema_name(template_key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", template_key)[:64] or "answer"


class OpenRouterProvider:
    """LLM gateway adapter for OpenRouter chat completions with structured output."""

    name = "openrouter"

    def __init__(
        self,
        api_key: Callable[[], str],
        selection: ModelSelection,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key, self._selection = api_key, selection
        self._http = client or httpx.Client(base_url=OPENROUTER_URL, timeout=180)

    def complete(self, request: LLMRequest) -> LLMResponse:
        model = self._selection.current()
        if not model:
            raise LLMError(
                f"{request.template_key}: no OpenRouter model chosen "
                "(Administration > AI model, or RAP_OPENROUTER_MODEL)"
            )
        try:
            key = self._api_key()
        except KeyError as exc:
            raise LLMError(f"{request.template_key}: {exc}") from exc
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": request.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(request.template_key),
                    "strict": True,
                    "schema": request.json_schema,
                },
            },
            # Only providers that honour every parameter here, so the schema is never dropped.
            "provider": {"require_parameters": True},
        }
        effort = self._selection.reasoning_effort(model, request.effort)
        if effort:
            body["reasoning"] = {"effort": effort, "exclude": True}
        try:
            response = self._http.post(
                "/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"{request.template_key}: cannot reach OpenRouter") from exc
        if response.status_code >= 400:
            raise LLMError(f"{request.template_key}: OpenRouter error {response.status_code}")
        return self._parse(request, model, response.json())

    @staticmethod
    def _parse(request: LLMRequest, model: str, data: dict[str, Any]) -> LLMResponse:
        if data.get("error"):
            code = (data["error"] or {}).get("code")
            raise LLMError(f"{request.template_key}: OpenRouter error {code}")
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"{request.template_key} returned no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            raise LLMRefused(f"{request.template_key} declined by {model}")
        if choice.get("finish_reason") == "length":
            raise LLMError(f"{request.template_key} hit max_tokens; raise the template limit")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise LLMError(f"{request.template_key} returned no text")
        fenced = _FENCE.match(text)
        try:
            answer = json.loads(fenced.group(1) if fenced else text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"{request.template_key}: {model} returned invalid JSON") from exc
        if not isinstance(answer, dict):
            raise LLMError(f"{request.template_key}: {model} returned JSON that is not an object")
        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        cost = usage.get("cost")  # OpenRouter credits, 1 credit = 1 USD
        return LLMResponse(
            data=answer,
            model=data.get("model") or model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cache_read_tokens=int(cached),
            reported_cost_usd=float(cost) if isinstance(cost, int | float) else None,
        )
