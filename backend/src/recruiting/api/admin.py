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
