"""Administrator API: platform users and roles (BRD section 9) and approvers per gate (Q14)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from recruiting.api.deps import ServicesDep, UserDep
from recruiting.approvals import GatePolicy, InvalidPolicy
from recruiting.domain import GateType, Role
from recruiting.identity import CurrentUser, DuplicateUser, InvalidUser, UnknownUser

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(user: CurrentUser) -> None:
    if user.role is not Role.ADMINISTRATOR:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: Role
    client_company_id: str | None
    active: bool


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    role: Role
    client_company_id: str | None = None


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    active: bool | None = None


class GatePolicyOut(BaseModel):
    gate: GateType
    first_approver_role: Role
    second_approver_role: Role | None


class GatePolicyIn(BaseModel):
    first_approver_role: Role
    second_approver_role: Role | None = None


def _user_out(user: CurrentUser) -> UserOut:
    return UserOut(
        id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        client_company_id=user.client_company_id,
        active=user.active,
    )


def _policy_out(policy: GatePolicy) -> GatePolicyOut:
    return GatePolicyOut(
        gate=policy.gate,
        first_approver_role=policy.first_approver,
        second_approver_role=policy.second_approver,
    )


@router.get("/users", response_model=list[UserOut])
def list_users(user: UserDep, services: ServicesDep) -> list[UserOut]:
    _require_admin(user)
    return [_user_out(u) for u in services.users.list_users()]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, user: UserDep, services: ServicesDep) -> UserOut:
    _require_admin(user)
    try:
        created = services.users.create_user(
            body.email, body.display_name, body.role, body.client_company_id, user.user_id
        )
    except DuplicateUser as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except InvalidUser as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return _user_out(created)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserUpdate, user: UserDep, services: ServicesDep) -> UserOut:
    _require_admin(user)
    # An Administrator cannot lock themselves out; another Administrator must do it.
    if user_id == user.user_id and (
        body.active is False or (body.role is not None and body.role is not Role.ADMINISTRATOR)
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You cannot deactivate or demote your own account"
        )
    try:
        updated = services.users.update_user(
            user_id,
            user.user_id,
            display_name=body.display_name,
            role=body.role,
            active=body.active,
        )
    except UnknownUser as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found") from exc
    except InvalidUser as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return _user_out(updated)


@router.get("/gate-policies", response_model=list[GatePolicyOut])
def list_gate_policies(user: UserDep, services: ServicesDep) -> list[GatePolicyOut]:
    _require_admin(user)
    policies = services.approvals.policies()
    return [_policy_out(policies[gate]) for gate in GateType]


@router.put("/gate-policies/{gate}", response_model=GatePolicyOut)
def update_gate_policy(
    gate: GateType, body: GatePolicyIn, user: UserDep, services: ServicesDep
) -> GatePolicyOut:
    _require_admin(user)
    policy = GatePolicy(gate, body.first_approver_role, body.second_approver_role)
    try:
        services.approvals.update_policy(policy, user.user_id)
    except InvalidPolicy as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return _policy_out(policy)


class LLMModelIn(BaseModel):
    model: str = Field(min_length=1, max_length=200)


def _model_out(model) -> dict:
    return {
        "id": model.id,
        "name": model.name,
        "tier": model.tier.value,
        "input_per_million": model.input_per_million,
        "output_per_million": model.output_per_million,
        "context_length": model.context_length,
        "reasoning": model.reasoning,
    }


@router.get("/llm-models")
def llm_models(user: UserDep, services: ServicesDep) -> dict:
    """The AI models the Administrator can choose from (RAP_LLM_PROVIDER=openrouter), live from
    OpenRouter, in Premium / Basic / Free tiers by price, and the one in use."""
    from recruiting.ai.openrouter import (
        PREMIUM_INPUT_PER_MILLION,
        TIER_ORDER,
        CatalogUnavailable,
        ModelTier,
    )

    _require_admin(user)
    platform = services.platform
    gateway = platform.gateway if platform else None
    provider = gateway.provider_name if gateway else "none"
    out: dict = {
        "provider": provider,
        "selected": None,
        "selected_model": None,
        "tiers": [],
        "catalog_error": None,
        "premium_input_per_million": None,
    }
    selection = platform.model_selection if platform else None
    if selection is None:
        return out
    out["premium_input_per_million"] = PREMIUM_INPUT_PER_MILLION
    out["selected"] = selection.current()
    try:
        models = selection.catalog.models()
    except CatalogUnavailable as exc:
        out["catalog_error"] = str(exc)
        return out
    for tier in sorted(ModelTier, key=TIER_ORDER.__getitem__):
        group = [_model_out(m) for m in models if m.tier is tier]
        out["tiers"].append({"tier": tier.value, "models": group})
    chosen = next((m for m in models if m.id == out["selected"]), None)
    out["selected_model"] = _model_out(chosen) if chosen else None
    return out


@router.put("/llm-model")
def set_llm_model(body: LLMModelIn, user: UserDep, services: ServicesDep) -> dict:
    """Choose the model the LLM gateway and the deep agents use from now on."""
    from recruiting.ai.openrouter import CatalogUnavailable, UnknownModel

    _require_admin(user)
    platform = services.platform
    selection = platform.model_selection if platform else None
    if selection is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Choosing a model needs RAP_LLM_PROVIDER=openrouter; Claude uses RAP_LLM_MODEL",
        )
    try:
        model = selection.choose(body.model, user.user_id)
    except UnknownModel as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except CatalogUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return _model_out(model)


@router.get("/llm-usage")
def llm_usage(user: UserDep, services: ServicesDep) -> list[dict]:
    """Cost tracking per prompt template version (plan section 5.4)."""
    _require_admin(user)
    platform = services.platform
    if platform is None or platform.gateway is None:
        return []
    totals: dict[tuple[str, str], dict] = {}
    for entry in platform.gateway.ledger.entries():
        row = totals.setdefault(
            (entry.template_key, entry.model),
            {"template": entry.template_key, "model": entry.model, "calls": 0,
             "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )  # fmt: skip
        row["calls"] += 1
        row["input_tokens"] += entry.input_tokens
        row["output_tokens"] += entry.output_tokens
        row["cost_usd"] = round(row["cost_usd"] + (entry.cost_usd or 0.0), 6)
    return sorted(totals.values(), key=lambda r: r["template"])
