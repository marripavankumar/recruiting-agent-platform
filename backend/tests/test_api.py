import pytest
from fastapi.testclient import TestClient

from recruiting.api.app import create_app
from recruiting.api.services import Services, build_services
from recruiting.approvals import ApprovalEngine
from recruiting.compliance import OptOutRegistry
from recruiting.config import Settings
from recruiting.domain import GateType, Role
from recruiting.identity import CurrentUser, DevHeaderAuthenticator, InMemoryUserDirectory

BD = CurrentUser("u-bd", "bd@cbs.example", "BD Manager", Role.BD_MANAGER)
RECRUITER = CurrentUser("u-rec", "rec@cbs.example", "Recruiter", Role.RECRUITER)
ADMIN = CurrentUser("u-admin", "admin@cbs.example", "Admin", Role.ADMINISTRATOR)
CLIENT = CurrentUser("u-client", "hm@client.example", "Client", Role.CLIENT, "company-9")
DRAFT = {"subject": "Engineering Talent Support", "body": "Dear Ms Lee, ..."}


def as_user(user: CurrentUser) -> dict[str, str]:
    return {"X-Dev-User-Email": user.email}


@pytest.fixture
def ctx():
    engine = ApprovalEngine()
    services = Services(
        approvals=engine,
        opt_outs=OptOutRegistry(),
        users=InMemoryUserDirectory([BD, RECRUITER, ADMIN, CLIENT]),
        authenticator=DevHeaderAuthenticator(),
    )
    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "contact-1", DRAFT, requested_by="agent-3")
    return TestClient(create_app(services)), request


def test_health(ctx):
    client, _ = ctx
    assert client.get("/health").json()["status"] == "ok"


def test_sign_in_required_and_unknown_users_refused(ctx):
    client, _ = ctx
    assert client.get("/approvals/pending").status_code == 401
    stranger = {"X-Dev-User-Email": "stranger@example.com"}
    assert client.get("/approvals/pending", headers=stranger).status_code == 403


def test_pending_queue_is_per_role(ctx):
    client, request = ctx
    bd_queue = client.get("/approvals/pending", headers=as_user(BD)).json()
    assert [r["id"] for r in bd_queue] == [request.id]
    assert bd_queue[0]["next_approver_role"] == "bd_manager"
    assert client.get("/approvals/pending", headers=as_user(RECRUITER)).json() == []
    assert client.get("/approvals/pending", headers=as_user(CLIENT)).status_code == 403


def test_visibility_of_a_single_request(ctx):
    client, request = ctx
    assert client.get(f"/approvals/{request.id}", headers=as_user(BD)).status_code == 200
    assert client.get(f"/approvals/{request.id}", headers=as_user(ADMIN)).status_code == 200
    assert client.get(f"/approvals/{request.id}", headers=as_user(RECRUITER)).status_code == 404
    assert client.get("/approvals/does-not-exist", headers=as_user(BD)).status_code == 404


def test_wrong_role_cannot_decide(ctx):
    client, request = ctx
    response = client.post(
        f"/approvals/{request.id}/decision", json={"action": "approve"}, headers=as_user(RECRUITER)
    )
    assert response.status_code == 403


def test_approve(ctx):
    client, request = ctx
    response = client.post(
        f"/approvals/{request.id}/decision", json={"action": "approve"}, headers=as_user(BD)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["next_approver_role"] is None
    assert body["decisions"][0]["reviewer_id"] == "u-bd"
    assert client.get("/approvals/pending", headers=as_user(BD)).json() == []


def test_reject_needs_reason_and_edit_changes_content(ctx):
    client, request = ctx
    url = f"/approvals/{request.id}/decision"
    assert client.post(url, json={"action": "reject"}, headers=as_user(BD)).status_code == 409
    assert client.post(url, json={"action": "maybe"}, headers=as_user(BD)).status_code == 422

    edited = {**DRAFT, "body": "Dear Ms Lee, edited"}
    response = client.post(
        url, json={"action": "edit_and_approve", "edited_content": edited}, headers=as_user(BD)
    )
    assert response.status_code == 200
    assert response.json()["content"] == edited


def test_dev_sign_in_refused_outside_local():
    with pytest.raises(RuntimeError):
        build_services(Settings(environment="prod", auth_provider="dev", storage="memory"))


def test_oidc_settings_required():
    with pytest.raises(RuntimeError):
        build_services(Settings(environment="prod", auth_provider="oidc", storage="memory"))


class FakeWorkflows:
    def __init__(self, fail: bool = False) -> None:
        self.resumed: list[str] = []
        self.fail = fail

    def resume(self, approval_request_id: str) -> None:
        self.resumed.append(approval_request_id)
        if self.fail:
            raise RuntimeError("mail provider down")


def client_with_workflows(workflows: FakeWorkflows, engine: ApprovalEngine) -> TestClient:
    services = Services(
        approvals=engine,
        opt_outs=OptOutRegistry(),
        users=InMemoryUserDirectory([BD, ADMIN]),
        authenticator=DevHeaderAuthenticator(),
        workflows=workflows,
    )
    return TestClient(create_app(services))


def test_decision_resumes_the_waiting_workflow():
    engine, workflows = ApprovalEngine(), FakeWorkflows()
    client = client_with_workflows(workflows, engine)
    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "contact-1", DRAFT, requested_by="agent-3")
    url = f"/approvals/{request.id}/decision"

    assert client.post(url, json={"action": "reject"}, headers=as_user(BD)).status_code == 409
    assert workflows.resumed == []  # refused decisions resume nothing
    client.post(url, json={"action": "approve"}, headers=as_user(BD))
    assert workflows.resumed == [request.id]


def test_workflow_failure_does_not_undo_the_decision():
    engine, workflows = ApprovalEngine(), FakeWorkflows(fail=True)
    client = client_with_workflows(workflows, engine)
    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "contact-1", DRAFT, requested_by="agent-3")
    response = client.post(
        f"/approvals/{request.id}/decision", json={"action": "approve"}, headers=as_user(BD)
    )
    assert response.status_code == 200
    assert engine.get(request.id).status == "approved"
    assert workflows.resumed == [request.id]


def test_api_docs_are_not_published_in_production():
    services = Services(
        approvals=ApprovalEngine(), opt_outs=OptOutRegistry(), users=InMemoryUserDirectory([]),
        authenticator=DevHeaderAuthenticator(),
    )  # fmt: skip
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert TestClient(create_app(services, environment="prod")).get(path).status_code == 404
        assert TestClient(create_app(services, environment="test")).get(path).status_code == 200
