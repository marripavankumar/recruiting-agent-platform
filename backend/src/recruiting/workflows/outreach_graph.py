"""LangGraph workflow: draft -> approval gate (interrupt) -> send through the Outreach Service.

The gate is split into two nodes because LangGraph re-runs an interrupted node from the start
on resume: `request_approval` has the side effect (creating the request) and runs once;
`await_approval` only pauses and then reads the decision.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from recruiting.approvals import ApprovalEngine, ApprovalStatus
from recruiting.domain import Channel, GateType
from recruiting.outreach import OutboundMessage, OutreachService, RecipientOptedOut


class OutreachState(TypedDict, total=False):
    gate: str
    subject_id: str
    requested_by: str
    channel: str
    recipient: str
    content: dict[str, str]
    approval_request_id: str
    approval_status: str
    delivery_status: str


def build_outreach_graph(
    approvals: ApprovalEngine, outreach: OutreachService, checkpointer: Any
) -> Any:
    def request_approval(state: OutreachState) -> OutreachState:
        request = approvals.submit(
            GateType(state["gate"]),
            state["subject_id"],
            state["content"],
            requested_by=state.get("requested_by", "agent"),
        )
        return {"approval_request_id": request.id}

    def await_approval(state: OutreachState) -> OutreachState:
        interrupt({"approval_request_id": state["approval_request_id"], "gate": state["gate"]})
        request = approvals.get(state["approval_request_id"])
        # The approved content may differ from the draft if the approver edited it.
        return {"approval_status": request.status.value, "content": dict(request.content)}

    def route_after_approval(state: OutreachState) -> str:
        status = state["approval_status"]
        if status == ApprovalStatus.APPROVED:
            return "send"
        if status == ApprovalStatus.REJECTED:
            return END
        return "await_approval"

    def send(state: OutreachState) -> OutreachState:
        message = OutboundMessage(
            approval_request_id=state["approval_request_id"],
            channel=Channel(state["channel"]),
            recipient=state["recipient"],
            content=state["content"],
        )
        try:
            receipt = outreach.send(message)
        except RecipientOptedOut:
            return {"delivery_status": "blocked_opted_out"}
        return {"delivery_status": receipt.status.value}

    graph = StateGraph(OutreachState)
    graph.add_node("request_approval", request_approval)
    graph.add_node("await_approval", await_approval)
    graph.add_node("send", send)
    graph.add_edge(START, "request_approval")
    graph.add_edge("request_approval", "await_approval")
    graph.add_conditional_edges(
        "await_approval", route_after_approval, ["send", "await_approval", END]
    )
    graph.add_edge("send", END)
    return graph.compile(checkpointer=checkpointer)
