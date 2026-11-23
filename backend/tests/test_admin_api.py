import pytest
from fastapi.testclient import TestClient

from recruiting.api.app import create_app
from recruiting.api.services import Services
from recruiting.approvals import ApprovalEngine, DecisionAction, Reviewer
from recruiting.compliance import OptOutRegistry
from recruiting.domain import GateType, Role
from recruiting.identity import CurrentUser, DevHeaderAuthenticator, InMemoryUserDirectory

ADMIN = CurrentUser("u-admin", "admin@cbs.example", "Admin", Role.ADMINISTRATOR)
BD = CurrentUser("u-bd", "bd@cbs.example", "BD Manager", Role.BD_MANAGER)
SALES = CurrentUser("u-sales", "sales@cbs.example", "Sales Manager", Role.SALES_MANAGER)
DRAFT = {"subject": "Engineering Talent Support", "body": "Dear Ms Lee, ..."}


def as_user(user: CurrentUser) -> dict[str, str]:
    return {"X-Dev-User-Email": user.email}


@pytest.fixture
def ctx():
    engine = ApprovalEngine()
    services = Services(
        approvals=engine,
        opt_outs=OptOutRegistry(),
        users=InMemoryUserDirectory([ADMIN, BD, SALES]),
        authenticator=DevHeaderAuthenticator(),
    )
    return TestClient(create_app(services)), engine


def test_only_administrators(ctx):
    client, _ = ctx
    assert client.get("/admin/users", headers=as_user(BD)).status_code == 403
    assert client.get("/admin/gate-policies", headers=as_user(BD)).status_code == 403
    assert client.get("/admin/users").status_code == 401


def test_create_list_and_duplicate_user(ctx):
    client, _ = ctx
    new = {"email": "rec2@cbs.example", "display_name": "Recruiter 2", "role": "recruiter"}
    created = client.post("/admin/users", json=new, headers=as_user(ADMIN))
    assert created.status_code == 201
    assert created.json()["active"] is True

    emails = [u["email"] for u in client.get("/admin/users", headers=as_user(ADMIN)).json()]
    assert "rec2@cbs.example" in emails
    again = client.post(
        "/admin/users", json={**new, "email": "REC2@cbs.example"}, headers=as_user(ADMIN)
    )
    assert again.status_code == 409

    # The new user can now sign in.
    assert (
        client.get(
            "/approvals/pending", headers={"X-Dev-User-Email": "rec2@cbs.example"}
        ).status_code
        == 200
    )


def test_client_users_need_a_company_and_staff_must_not_have_one(ctx):
    client, _ = ctx
    base = {"email": "hm@client.example", "display_name": "Hiring Manager"}
    no_company = client.post(
        "/admin/users", json={**base, "role": "client"}, headers=as_user(ADMIN)
    )
    assert no_company.status_code == 422
    staff_with_company = client.post(
        "/admin/users",
        json={**base, "role": "recruiter", "client_company_id": "c-1"},
        headers=as_user(ADMIN),
    )
    assert staff_with_company.status_code == 422
    assert (
        client.post(
            "/admin/users", json={**base, "email": "bad"}, headers=as_user(ADMIN)
        ).status_code
        == 422
    )


def test_deactivated_user_cannot_sign_in(ctx):
    client, _ = ctx
    response = client.patch(
        f"/admin/users/{BD.user_id}", json={"active": False}, headers=as_user(ADMIN)
    )
    assert response.status_code == 200
    assert response.json()["active"] is False
    assert client.get("/approvals/pending", headers=as_user(BD)).status_code == 403


def test_role_change_and_unknown_user(ctx):
    client, _ = ctx
    response = client.patch(
        f"/admin/users/{BD.user_id}", json={"role": "account_manager"}, headers=as_user(ADMIN)
    )
    assert response.json()["role"] == "account_manager"
    assert (
        client.patch(
            "/admin/users/nobody", json={"active": False}, headers=as_user(ADMIN)
        ).status_code
        == 404
    )


def test_administrator_cannot_lock_themselves_out(ctx):
    client, _ = ctx
    url = f"/admin/users/{ADMIN.user_id}"
    assert client.patch(url, json={"active": False}, headers=as_user(ADMIN)).status_code == 409
    assert client.patch(url, json={"role": "recruiter"}, headers=as_user(ADMIN)).status_code == 409
    assert (
        client.patch(url, json={"display_name": "Admin 1"}, headers=as_user(ADMIN)).status_code
        == 200
    )


def test_gate_policies_default_to_q14(ctx):
    client, _ = ctx
    policies = {
        p["gate"]: p for p in client.get("/admin/gate-policies", headers=as_user(ADMIN)).json()
    }
    assert policies["first_client_email"]["first_approver_role"] == "bd_manager"
    assert policies["resume_submission"]["second_approver_role"] is None
    assert len(policies) == 4


def test_adding_a_second_approver_applies_to_pending_requests(ctx):
    client, engine = ctx
    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "contact-1", DRAFT, requested_by="agent-3")
    response = client.put(
        "/admin/gate-policies/first_client_email",
        json={"first_approver_role": "bd_manager", "second_approver_role": "sales_manager"},
        headers=as_user(ADMIN),
    )
    assert response.status_code == 200

    engine.decide(request.id, Reviewer(BD.user_id, Role.BD_MANAGER), DecisionAction.APPROVE)
    assert engine.get(request.id).status == "pending"
    sales_queue = client.get("/approvals/pending", headers=as_user(SALES)).json()
    assert [r["id"] for r in sales_queue] == [request.id]


def test_invalid_gate_policies_are_refused(ctx):
    client, _ = ctx
    url = "/admin/gate-policies/first_client_email"
    client_approver = {"first_approver_role": "client"}
    assert client.put(url, json=client_approver, headers=as_user(ADMIN)).status_code == 422
    unknown_gate = "/admin/gate-policies/no_such_gate"
    assert (
        client.put(
            unknown_gate, json={"first_approver_role": "bd_manager"}, headers=as_user(ADMIN)
        ).status_code
        == 422
    )
