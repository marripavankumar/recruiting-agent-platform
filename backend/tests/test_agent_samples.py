"""Sample AI agent runs in the demo and test data: the history is readable while the agents are
off, each role sees only the agents it may run, and nothing can be started or decided."""

from fastapi.testclient import TestClient

from recruiting.api.app import create_app
from recruiting.api.services import build_services
from recruiting.config import Settings


def _as(email: str) -> dict[str, str]:
    return {"X-Dev-User-Email": email}


def test_demo_has_sample_runs_that_follow_the_roles_and_cannot_be_acted_on():
    services = build_services(Settings(demo_data=True, _env_file=None))
    client = TestClient(create_app(services))
    info = client.get("/agents", headers=_as("recruiter@cbs.example")).json()
    assert info["enabled"] is False and info["has_history"] is True

    admin = client.get("/agents/runs", headers=_as("administrator@cbs.example")).json()
    assert {r["agent"] for r in admin} == {
        "recruiting", "business_development", "follow_up", "intake"
    }  # fmt: skip
    assert {r["status"] for r in admin} == {"done", "waiting_for_human", "failed"}
    assert all(r["started_by_name"].startswith("Demo ") for r in admin)
    assert all("Sample run" in r["summary"] for r in admin if r["status"] == "done")

    jobs = {j["id"] for j in client.get("/jobs", headers=_as("recruiter@cbs.example")).json()}
    recruiter = client.get("/agents/runs", headers=_as("recruiter@cbs.example")).json()
    assert {r["agent"] for r in recruiter} == {"recruiting", "follow_up"}
    assert all(r["subject_id"] in jobs for r in recruiter if r["agent"] == "recruiting")
    bd = client.get("/agents/runs", headers=_as("bd.manager@cbs.example")).json()
    assert "recruiting" not in {r["agent"] for r in bd}

    waiting = next(r for r in recruiter if r["status"] == "waiting_for_human")
    assert waiting["pending"][0]["action_requests"][0]["name"] == "widen_search"
    detail = client.get(f"/agents/runs/{waiting['id']}", headers=_as("recruiter@cbs.example"))
    assert detail.status_code == 200 and detail.json()["started_by_name"] == "Demo Recruiter"
    assert client.get(f"/agents/runs/{waiting['id']}",
                      headers=_as("bd.manager@cbs.example")).status_code == 403  # fmt: skip

    # Agents off: the history is shown, but nothing runs.
    decide = client.post(f"/agents/runs/{waiting['id']}/decisions", headers=_as("recruiter@cbs.example"),
                         json={"decisions": [{"type": "approve"}]})  # fmt: skip
    assert decide.status_code == 503
    start = client.post("/agents/follow_up/runs", json={}, headers=_as("recruiter@cbs.example"))
    assert start.status_code == 503
