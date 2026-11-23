from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from recruiting.ai.llm import (
    ClaudeProvider,
    LLMError,
    LLMGateway,
    LLMRefused,
    LLMRequest,
    LLMResponse,
    PromptRegistry,
    PromptTemplate,
    ScriptedProvider,
    cost_usd,
    strict_schema,
)
from recruiting.ai.prompts import ClientEmailDraft, JDExtraction, default_registry


class Answer(BaseModel):
    title: str
    tags: list[str]


T1 = PromptTemplate("demo", 1, "system v1", "Say $thing", Answer, effort="low")
T2 = PromptTemplate("demo", 2, "system v2", "Say $thing please", Answer)


def test_registry_keeps_versions_and_returns_latest():
    registry = PromptRegistry([T1, T2])
    assert registry.get("demo").key == "demo@2"
    assert registry.get("demo", 1).system == "system v1"
    with pytest.raises(ValueError):
        registry.register(T1)


def test_strict_schema_closes_objects_and_keeps_a_property_named_title():
    schema = strict_schema(Answer)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["title", "tags"]
    assert "title" in schema["properties"] and "title" not in schema
    nested = strict_schema(JDExtraction)["$defs"]["ExtractedJob"]
    assert nested["additionalProperties"] is False and "title" in nested["required"]


def test_gateway_validates_output_and_records_cost_per_template_version():
    provider = ScriptedProvider({"demo": lambda req: {"title": "hi", "tags": ["a"]}})
    gateway = LLMGateway(provider, PromptRegistry([T1, T2]))
    answer = gateway.run("demo", Answer, thing="hello")
    assert answer == Answer(title="hi", tags=["a"])
    assert provider.requests[0].user == "Say hello please"
    assert provider.requests[0].json_schema["additionalProperties"] is False
    [entry] = gateway.ledger.entries()
    assert entry.template_key == "demo@2" and entry.provider == "scripted"
    with pytest.raises(TypeError):
        gateway.run("demo", ClientEmailDraft, thing="x")


def test_cost_uses_cache_read_price():
    response = LLMResponse({}, "claude-opus-5-5", 1_000_000, 100_000, cache_read_tokens=500_000)
    assert cost_usd(response) == pytest.approx(0.5 * 4 + 0.1 * 20 + 0.5 * 0.2)
    assert cost_usd(LLMResponse({}, "unknown-model", 10, 10)) is None


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def message(stop_reason="end_turn", text='{"title": "x", "tags": []}', category=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if category else None,
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=120, output_tokens=30, cache_read_input_tokens=None),
    )


REQUEST = LLMRequest("demo@1", "sys", "user text", strict_schema(Answer), "low", 2000)


def test_claude_provider_sends_structured_output_effort_and_fallbacks():
    messages = FakeMessages(message())
    result = ClaudeProvider(fake_client(messages)).complete(REQUEST)
    assert result.data == {"title": "x", "tags": []}
    assert (result.input_tokens, result.output_tokens, result.cache_read_tokens) == (120, 30, 0)
    [call] = messages.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["messages"] == [{"role": "user", "content": "user text"}]
    assert "temperature" not in call and "thinking" not in call


def test_claude_provider_refusal_and_truncation():
    with pytest.raises(LLMRefused):
        ClaudeProvider(fake_client(FakeMessages(message("refusal", category="cyber")))).complete(
            REQUEST
        )
    with pytest.raises(LLMError):
        ClaudeProvider(fake_client(FakeMessages(message("max_tokens")))).complete(REQUEST)


def test_sdk_errors_become_llm_errors_so_agents_can_fall_back():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    connection = anthropic.APIConnectionError(request=request)
    with pytest.raises(LLMError):
        ClaudeProvider(fake_client(FakeMessages(error=connection))).complete(REQUEST)
    status = anthropic.AuthenticationError(
        "bad key", response=httpx.Response(401, request=request), body=None
    )
    with pytest.raises(LLMError):
        ClaudeProvider(fake_client(FakeMessages(error=status))).complete(REQUEST)


def test_default_registry_templates_render():
    registry = default_registry()
    email = registry.get("first_client_email")
    rendered = email.render(
        dict(company="A", industry="B", contact_name="C", contact_title="D", roles="E",
             location="F", sender_name="G")
    )  # fmt: skip
    assert "Company: A" in rendered and "Sender: G" in rendered
    for name in ("reply_classification", "jd_extraction", "jd_analysis"):
        assert strict_schema(registry.get(name).output)["additionalProperties"] is False
