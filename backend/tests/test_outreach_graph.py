import pytest

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from recruiting.approvals import ApprovalEngine, DecisionAction, Reviewer  # noqa: E402
from recruiting.compliance import OptOutRegistry  # noqa: E402
from recruiting.domain import Role  # noqa: E402
from recruiting.outreach import DeliveryResult, DeliveryStatus, OutreachService  # noqa: E402
from recruiting.workflows.outreach_graph import build_outreach_graph  # noqa: E402


class FakeEmailChannel:
    channel = "email"

    def __init__(self):
        self.sent = []

    def deliver(self, message):
        self.sent.append(message)
        return DeliveryResult(DeliveryStatus.SENT, "msg-1")


def make_graph():
    engine = ApprovalEngine()
    email = FakeEmailChannel()
    service = OutreachService(engine, OptOutRegistry(), [email])
    return engine, email, build_outreach_graph(engine, service, MemorySaver())


START_STATE = {
    "gate": "first_client_email",
    "subject_id": "company-1",
    "requested_by": "agent-3",
    "channel": "email",
    "recipient": "lee@example.com",
    "content": {"subject": "Engineering Talent Support", "body": "Dear Ms Lee, ..."},
}


def test_graph_pauses_at_gate_and_sends_edited_version_after_approval():
    engine, email, graph = make_graph()
    config = {"configurable": {"thread_id": "t-1"}}

    graph.invoke(START_STATE, config)
    snapshot = graph.get_state(config)
    assert snapshot.next == ("await_approval",)
    assert email.sent == []

    request_id = snapshot.values["approval_request_id"]
    edited = {**START_STATE["content"], "body": "Dear Ms Lee, edited by BD"}
    engine.decide(
        request_id,
        Reviewer("u-bd", Role.BD_MANAGER),
        DecisionAction.EDIT_AND_APPROVE,
        edited_content=edited,
    )

    final = graph.invoke(Command(resume=True), config)
    assert final["delivery_status"] == "sent"
    assert [m.content for m in email.sent] == [edited]


def test_rejected_request_sends_nothing():
    engine, email, graph = make_graph()
    config = {"configurable": {"thread_id": "t-2"}}
    graph.invoke(START_STATE, config)
    request_id = graph.get_state(config).values["approval_request_id"]
    engine.decide(
        request_id, Reviewer("u-bd", Role.BD_MANAGER), DecisionAction.REJECT, reason="Not a fit"
    )

    final = graph.invoke(Command(resume=True), config)
    assert final["approval_status"] == "rejected"
    assert "delivery_status" not in final
    assert email.sent == []


def test_resume_before_decision_pauses_again():
    engine, email, graph = make_graph()
    config = {"configurable": {"thread_id": "t-3"}}
    graph.invoke(START_STATE, config)
    graph.invoke(Command(resume=True), config)
    assert graph.get_state(config).next == ("await_approval",)
    assert len(engine.pending_for(Role.BD_MANAGER)) == 1
    assert email.sent == []
