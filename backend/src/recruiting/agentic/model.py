"""The chat model behind the deep agents, and LLM cost tracking for agent runs (plan 5.4).

Claude Opus 5.5 through langchain-anthropic (which uses the official Anthropic SDK): thinking is
always on for this model, so depth is set with `effort` (its default is `medium`; agentic tool
use is set explicitly, `high` by default). Server-side refusal fallback is on, as for the
gateway's single calls. The agents never force a tool call (the model rejects forced
tool_choice); they are steered by their prompts.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import LLMResult

from recruiting.ai.llm import PRICES, UsageLedger, UsageRecord

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def build_chat_model(
    model: str = "claude-opus-5-5", effort: str = "high", max_tokens: int = 16000
) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=model,
        max_tokens=max_tokens,
        output_config={"effort": effort},
        betas=[FALLBACK_BETA],
        model_kwargs={"fallbacks": "default"},
        max_retries=2,
    )


def build_openrouter_chat_model(
    model: str, api_key: str, effort: str | None = None, max_tokens: int = 16000
) -> BaseChatModel:
    """Any OpenRouter model with tool calls, through its OpenAI-compatible API. Requests go only
    to providers that support every parameter sent (tools included); `effort` is sent only for
    models that take a reasoning setting (ModelSelection.reasoning_effort)."""
    from langchain_openai import ChatOpenAI

    from recruiting.ai.openrouter import OPENROUTER_URL

    extra: dict[str, Any] = {"provider": {"require_parameters": True}}
    if effort:
        extra["reasoning"] = {"effort": effort, "exclude": True}
    return ChatOpenAI(
        model=model,
        base_url=OPENROUTER_URL,
        api_key=api_key,
        max_tokens=max_tokens,
        max_retries=2,
        extra_body=extra,
    )


def _cost(model: str, input_tokens: int, output_tokens: int, cache_read: int) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    uncached = max(input_tokens - cache_read, 0)
    return (uncached * price[0] + output_tokens * price[1] + cache_read * price[2]) / 1_000_000


class UsageCallback(BaseCallbackHandler):
    """Records the tokens and cost of every model call an agent makes, per agent."""

    def __init__(
        self,
        ledger: UsageLedger,
        agent: str,
        model: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        provider: str = "claude",
    ) -> None:
        self._ledger, self._agent, self._model, self._clock = ledger, agent, model, clock
        self._provider = provider

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if not usage:
                    continue
                details = usage.get("input_token_details") or {}
                cache_read = int(details.get("cache_read") or 0)
                metadata = getattr(message, "response_metadata", None) or {}
                model = metadata.get("model_name", self._model)
                # OpenRouter reports the cost of each call itself (usage.cost, in USD).
                reported = (metadata.get("token_usage") or {}).get("cost")
                self._ledger.record(
                    UsageRecord(
                        template_key=f"agent:{self._agent}",
                        provider=self._provider,
                        model=model,
                        input_tokens=int(usage.get("input_tokens") or 0),
                        output_tokens=int(usage.get("output_tokens") or 0),
                        cost_usd=float(reported)
                        if isinstance(reported, int | float)
                        else _cost(
                            model,
                            int(usage.get("input_tokens") or 0),
                            int(usage.get("output_tokens") or 0),
                            cache_read,
                        ),
                        at=self._clock(),
                    )
                )
