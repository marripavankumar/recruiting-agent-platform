"""OpenRouter: the live model list in tiers, the Administrator's choice, the gateway adapter and
the deep agents' model. OpenRouter itself is replaced by an httpx mock transport."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from recruiting.ai.llm import (
    InMemoryUsageLedger,
    LLMError,
    LLMGateway,
    LLMRefused,
    LLMRequest,
    PromptRegistry,
    PromptTemplate,
)
from recruiting.ai.openrouter import (
    OPENROUTER_URL,
    CatalogUnavailable,
    InMemoryModelChoice,
    ModelSelection,
    ModelTier,
    OpenRouterCatalog,
    OpenRouterProvider,
    UnknownModel,
    tier_for,
)
from recruiting.api.app import create_app
from recruiting.api.platform import build_platform
from recruiting.api.services import Services
from recruiting.approvals import ApprovalEngine
from recruiting.compliance import OptOutRegistry
from recruiting.config import Settings
from recruiting.domain import Role
from recruiting.identity import CurrentUser, DevHeaderAuthenticator, InMemoryUserDirectory
from recruiting.outreach import OutreachService

ADMIN = CurrentUser("u-admin", "admin@cbs.example", "Admin", Role.ADMINISTRATOR)
REC = CurrentUser("u-rec", "rec@cbs.example", "Recruiter", Role.RECRUITER)

MODELS = [
    {
        "id": "vendor/big",
        "name": "Vendor: Big",
        "pricing": {"prompt": "0.000004", "completion": "0.00002"},
        "context_length": 200000,
        "supported_parameters": ["tools", "structured_outputs", "reasoning", "response_format"],
        "reasoning": {"supported_efforts": ["low", "medium", "high"]},
    },
    {
        "id": "vendor/small",
        "name": "Vendor: Small",
        "pricing": {"prompt": "0.0000001", "completion": "0.0000004"},
        "context_length": 128000,
        "supported_parameters": ["tools", "structured_outputs"],
    },
    {
        "id": "vendor/open:free",
        "name": "Vendor: Open (free)",
        "pricing": {"prompt": "0", "completion": "0"},
        "supported_parameters": ["tools", "structured_outputs"],
    },
    # Not offered: a router with no fixed price, and a model without tool calls.
    {"id": "openrouter/auto", "name": "Auto", "pricing": {"prompt": "-1", "completion": "-1"}},
    {
        "id": "vendor/no-tools",
        "name": "No tools",
        "pricing": {"prompt": "0.000002", "completion": "0.000002"},
        "supported_parameters": ["structured_outputs"],
    },
]


class Answer(BaseModel):
    title: str


class FakeOpenRouter:
    """Serves GET /models and POST /chat/completions; records the requests."""

    def __init__(self, models=MODELS, reply=None):
        self.models, self.requests, self.fail_models = models, [], False
        self.reply = reply or completion('{"title": "Controls Engineer"}')

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models"):
            if self.fail_models:
                return httpx.Response(503)
            return httpx.Response(200, json={"data": self.models})
        reply = self.reply(request) if callable(self.reply) else self.reply
        return reply

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=OPENROUTER_URL, transport=httpx.MockTransport(self.handler))


def completion(content, finish="stop", cost=0.0123, **message):
    return httpx.Response(
        200,
        json={
            "model": "vendor/big",
            "choices": [
                {"finish_reason": finish, "message": {"role": "assistant", "content": content,
                                                      **message}}
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "cost": cost,
                      "prompt_tokens_details": {"cached_tokens": 20}},
        },
    )  # fmt: skip


def request(effort="high"):
    return LLMRequest("jd_extract@3", "system", "user text", {"type": "object"}, effort, 1000)


def selection(fake, chosen="vendor/big"):
    store = InMemoryModelChoice()
    if chosen:
        store.save(chosen, "u-admin")
    return ModelSelection(OpenRouterCatalog(fake.client()), store)


# ----- catalog ----------------------------------------------------------------------------------


def test_tiers_by_price():
    assert tier_for(0, 0) is ModelTier.FREE
    assert tier_for(0.15, 0.6) is ModelTier.BASIC
    assert tier_for(1.0, 4.0) is ModelTier.PREMIUM


def test_catalog_lists_only_usable_models_in_tier_order():
    fake = FakeOpenRouter()
    models = OpenRouterCatalog(fake.client()).models()

    assert [(m.id, m.tier) for m in models] == [
        ("vendor/big", ModelTier.PREMIUM),
        ("vendor/small", ModelTier.BASIC),
        ("vendor/open:free", ModelTier.FREE),
    ]
    big = models[0]
    assert (big.input_per_million, big.output_per_million) == (4.0, 20.0)
    assert (
        big.context_length == 200000 and big.reasoning and big.efforts == ("low", "medium", "high")
    )
    # The server is asked to filter too; no key is sent for the public list.
    sent = fake.requests[0]
    assert sent.url.params["supported_parameters"] == "tools,structured_outputs"
    assert "authorization" not in sent.headers


def test_catalog_is_cached_and_keeps_the_last_good_list():
    fake, now = FakeOpenRouter(), [datetime(2026, 11, 27, tzinfo=UTC)]
    catalog = OpenRouterCatalog(fake.client(), clock=lambda: now[0])
    catalog.models()
    catalog.models()
    assert len(fake.requests) == 1

    now[0] += timedelta(hours=2)
    fake.fail_models = True
    assert len(catalog.models()) == 3  # OpenRouter down: the earlier list is kept
    assert len(fake.requests) == 2


def test_catalog_unavailable_without_an_earlier_list():
    fake = FakeOpenRouter()
    fake.fail_models = True
    with pytest.raises(CatalogUnavailable):
        OpenRouterCatalog(fake.client()).models()


def test_choose_refuses_models_not_offered():
    sel = selection(FakeOpenRouter(), chosen=None)
    with pytest.raises(UnknownModel):
        sel.choose("vendor/no-tools", "u-admin")
    assert sel.choose("vendor/small", "u-admin").tier is ModelTier.BASIC
    assert sel.current() == "vendor/small"


def test_default_model_until_one_is_chosen():
    sel = ModelSelection(OpenRouterCatalog(FakeOpenRouter().client()), InMemoryModelChoice(), "x/y")
    assert sel.current() == "x/y"
    sel.choose("vendor/big", "u-admin")
    assert sel.current() == "vendor/big"


def test_reasoning_effort_fits_the_model():
    sel = selection(FakeOpenRouter())
    assert sel.reasoning_effort("vendor/big", "medium") == "medium"
    assert sel.reasoning_effort("vendor/big", "max") == "high"  # nearest lower it accepts
    assert sel.reasoning_effort("vendor/small", "high") is None  # no reasoning setting


# ----- gateway adapter --------------------------------------------------------------------------


def test_provider_sends_strict_schema_and_records_reported_cost():
    fake = FakeOpenRouter(reply=completion('```json\n{"title": "Controls Engineer"}\n```'))
    provider = OpenRouterProvider(lambda: "sk-or-test", selection(fake), fake.client())
    ledger = InMemoryUsageLedger()
    gateway = LLMGateway(
        provider, PromptRegistry([PromptTemplate("t", 1, "sys", "Say $x", Answer)]), ledger
    )

    assert gateway.run("t", Answer, x="hi").title == "Controls Engineer"

    sent = fake.requests[-1]
    body = json.loads(sent.content)
    assert sent.headers["authorization"] == "Bearer sk-or-test"
    assert body["model"] == "vendor/big"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["name"] == "t_1"
    assert body["provider"] == {"require_parameters": True}
    assert body["reasoning"] == {"effort": "medium", "exclude": True}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    entry = ledger.entries()[0]
    assert (entry.provider, entry.model, entry.cost_usd) == ("openrouter", "vendor/big", 0.0123)
    assert gateway.provider_name == "openrouter"


def test_provider_without_reasoning_sends_no_effort():
    fake = FakeOpenRouter()
    OpenRouterProvider(lambda: "k", selection(fake, "vendor/small"), fake.client()).complete(
        request()
    )
    assert "reasoning" not in json.loads(fake.requests[-1].content)


@pytest.mark.parametrize(
    ("reply", "error", "match"),
    [
        (completion("", refusal="I can't help with that"), LLMRefused, "declined"),
        (completion('{"title": "x"', finish="length"), LLMError, "max_tokens"),
        (completion("not json"), LLMError, "invalid JSON"),
        (completion("[1, 2]"), LLMError, "not an object"),
        (httpx.Response(429, json={"error": {"code": 429}}), LLMError, "error 429"),
        (httpx.Response(200, json={"error": {"code": 502, "message": "upstream"}}), LLMError,
         "error 502"),
    ],
)  # fmt: skip
def test_provider_errors_become_llm_errors(reply, error, match):
    fake = FakeOpenRouter(reply=reply)
    provider = OpenRouterProvider(lambda: "k", selection(fake), fake.client())
    with pytest.raises(error, match=match):
        provider.complete(request())


def test_provider_needs_a_model_and_a_key():
    fake = FakeOpenRouter()
    with pytest.raises(LLMError, match="no OpenRouter model chosen"):
        OpenRouterProvider(lambda: "k", selection(fake, None), fake.client()).complete(request())

    def missing():
        raise KeyError("Secret openrouter_api_key not set")

    with pytest.raises(LLMError, match="openrouter_api_key"):
        OpenRouterProvider(missing, selection(fake), fake.client()).complete(request())


def test_provider_cannot_reach_openrouter():
    def down(_request):
        raise httpx.ConnectError("refused")

    client = httpx.Client(base_url=OPENROUTER_URL, transport=httpx.MockTransport(down))
    sel = ModelSelection(OpenRouterCatalog(FakeOpenRouter().client()), InMemoryModelChoice(), "a/b")
    with pytest.raises(LLMError, match="cannot reach"):
        OpenRouterProvider(lambda: "k", sel, client).complete(request())


# ----- Administrator API ------------------------------------------------------------------------


def admin_client(provider="openrouter", fake=None):
    settings = Settings(llm_provider=provider, _env_file=None)
    users = InMemoryUserDirectory([ADMIN, REC])
    approvals, opt_outs = ApprovalEngine(), OptOutRegistry()
    outreach = OutreachService(approvals, opt_outs, [])
    catalog = OpenRouterCatalog((fake or FakeOpenRouter()).client())
    platform = build_platform(
        settings, users=users, opt_outs=opt_outs, deliveries=outreach.log, workflows=None,
        model_catalog=catalog,
    )  # fmt: skip
    services = Services(approvals, opt_outs, users, DevHeaderAuthenticator(), None, outreach,
                        platform)  # fmt: skip
    return TestClient(create_app(services)), platform


def h(user):
    return {"X-Dev-User-Email": user.email}


def test_admin_lists_models_by_tier_and_chooses_one():
    client, platform = admin_client()
    listing = client.get("/admin/llm-models", headers=h(ADMIN)).json()
    assert listing["provider"] == "openrouter" and listing["selected"] is None
    assert [t["tier"] for t in listing["tiers"]] == ["premium", "basic", "free"]
    assert [[m["id"] for m in t["models"]] for t in listing["tiers"]] == [
        ["vendor/big"],
        ["vendor/small"],
        ["vendor/open:free"],
    ]
    assert listing["premium_input_per_million"] == 1.0

    chosen = client.put("/admin/llm-model", json={"model": "vendor/small"}, headers=h(ADMIN))
    assert chosen.status_code == 200 and chosen.json()["tier"] == "basic"
    after = client.get("/admin/llm-models", headers=h(ADMIN)).json()
    assert after["selected"] == "vendor/small"
    assert after["selected_model"]["name"] == "Vendor: Small"
    assert platform.model_selection.current() == "vendor/small"

    refused = client.put("/admin/llm-model", json={"model": "vendor/no-tools"}, headers=h(ADMIN))
    assert refused.status_code == 422


def test_admin_model_choice_is_for_administrators_only():
    client, _ = admin_client()
    assert client.get("/admin/llm-models", headers=h(REC)).status_code == 403
    put = client.put("/admin/llm-model", json={"model": "vendor/big"}, headers=h(REC))
    assert put.status_code == 403


def test_admin_model_list_when_openrouter_is_down():
    fake = FakeOpenRouter()
    fake.fail_models = True
    client, _ = admin_client(fake=fake)
    listing = client.get("/admin/llm-models", headers=h(ADMIN)).json()
    assert listing["tiers"] == [] and "Cannot read" in listing["catalog_error"]
    put = client.put("/admin/llm-model", json={"model": "vendor/big"}, headers=h(ADMIN))
    assert put.status_code == 503


def test_choosing_a_model_needs_openrouter():
    client, _ = admin_client(provider="none")
    listing = client.get("/admin/llm-models", headers=h(ADMIN)).json()
    assert listing["provider"] == "none" and listing["tiers"] == []
    put = client.put("/admin/llm-model", json={"model": "vendor/big"}, headers=h(ADMIN))
    assert put.status_code == 409


# ----- deep agents ------------------------------------------------------------------------------


def test_openrouter_chat_model_for_the_deep_agents():
    pytest.importorskip("langchain_openai")
    from recruiting.agentic.model import build_openrouter_chat_model

    model = build_openrouter_chat_model("vendor/big", "sk-or-test", "high")
    assert model.model_name == "vendor/big"
    assert model.openai_api_base == OPENROUTER_URL
    assert model.extra_body == {
        "provider": {"require_parameters": True},
        "reasoning": {"effort": "high", "exclude": True},
    }
    assert "reasoning" not in build_openrouter_chat_model("vendor/small", "k").extra_body


def test_agent_run_uses_the_model_chosen_now_and_fails_visibly_without_one():
    pytest.importorskip("deepagents")
    from langgraph.checkpoint.memory import MemorySaver

    from recruiting.agentic.runs import AgentRunner, InMemoryAgentRuns, RunStatus
    from recruiting.agentic.testing import ScriptedChatModel, say

    _, platform = admin_client()
    used = []

    def source():
        model = platform.model_selection.current()
        if not model:
            raise RuntimeError("No OpenRouter model chosen")
        used.append(model)
        return ScriptedChatModel(script=[say("Nothing is due.")]), model

    runner = AgentRunner(
        platform, None, InMemoryAgentRuns(), MemorySaver(), model_source=source,
        provider="openrouter",
    )  # fmt: skip
    failed = runner.start("follow_up", None, ADMIN.user_id)
    assert failed.status is RunStatus.FAILED and "No OpenRouter model chosen" in failed.error

    platform.model_selection.choose("vendor/small", ADMIN.user_id)
    done = runner.start("follow_up", None, ADMIN.user_id)
    assert done.status is RunStatus.DONE and used == ["vendor/small"]
