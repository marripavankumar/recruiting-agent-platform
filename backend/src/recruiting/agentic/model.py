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
    ) -> None:
        self._ledger, self._agent, self._model, self._clock = ledger, agent, model, clock

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if not usage:
                    continue
                details = usage.get("input_token_details") or {}
                cache_read = int(details.get("cache_read") or 0)
                model = (getattr(message, "response_metadata", None) or {}).get(
                    "model_name", self._model
                )
                self._ledger.record(
                    UsageRecord(
                        template_key=f"agent:{self._agent}",
                        provider="claude",
                        model=model,
                        input_tokens=int(usage.get("input_tokens") or 0),
                        output_tokens=int(usage.get("output_tokens") or 0),
                        cost_usd=_cost(
                            model,
                            int(usage.get("input_tokens") or 0),
                            int(usage.get("output_tokens") or 0),
                            cache_read,
                        ),
                        at=self._clock(),
                    )
                )
