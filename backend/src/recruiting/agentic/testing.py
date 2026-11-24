"""A scripted chat model for testing the deep agents without calling an LLM.

Each model call pops the next scripted reply. A reply is an AIMessage, or a callable that gets
the conversation so far and returns one (to use ids or values produced by earlier tool calls).
The model records the tool names it was offered and the messages it saw.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

Reply = AIMessage | Callable[[list[BaseMessage]], AIMessage]


def call(tool: str, call_id: str | None = None, **args: Any) -> AIMessage:
    """An assistant turn that calls one tool."""
    return AIMessage(
        content="", tool_calls=[{"name": tool, "args": args, "id": call_id or f"call-{tool}"}]
    )


def say(text: str) -> AIMessage:
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050},
    )


class ScriptedChatModel(BaseChatModel):
    script: list[Any] = []
    offered: list[list[str]] = []
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        names = []
        for t in tools:
            name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
            if name is None and isinstance(t, dict):
                name = (t.get("function") or {}).get("name")
            names.append(name)
        self.offered.append(names)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        if not self.script:
            raise AssertionError("The scripted model ran out of replies")
        reply = self.script.pop(0)
        message = reply(list(messages)) if callable(reply) else reply
        return ChatResult(generations=[ChatGeneration(message=message)])
