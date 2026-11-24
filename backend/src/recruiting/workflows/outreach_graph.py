"""LangGraph workflow: draft -> approval gate (interrupt) -> send through the Outreach Service.

The gate is split into two nodes because LangGraph re-runs an interrupted node from the start
on resume: `request_approval` has the side effect (creating the request) and runs once;
`await_approval` only pauses and then reads the decision.

`OutreachWorkflow` runs one thread per approval request and uses the request id as the thread
id, so the review-queue API can resume the right thread once a request is decided.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from recruiting.approvals import ApprovalEngine, ApprovalStatus
from recruiting.domain import Channel, GateType
from recruiting.outreach import (
    DeliveryDeferred,
    OutboundMessage,
    OutreachDisabled,
    OutreachService,
    RecipientOptedOut,
)


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
            request_id=state.get("approval_request_id"),
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
        except OutreachDisabled:
            return {"delivery_status": "blocked_outreach_disabled"}
        except DeliveryDeferred:
            # S24: kept under the same approval; OutreachService.retry_due sends it later.
            return {"delivery_status": "retry_scheduled"}
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


def _thread(approval_request_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": approval_request_id}}


class OutreachWorkflow:
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    def start(
        self,
        *,
        gate: GateType,
        subject_id: str,
        channel: Channel,
        recipient: str,
        content: dict[str, str],
        requested_by: str = "agent",
    ) -> str:
        """Create the approval request and pause at the gate. Returns the request id."""
        request_id = str(uuid.uuid4())
        state: OutreachState = {
            "gate": gate.value,
            "subject_id": subject_id,
            "requested_by": requested_by,
            "channel": channel.value,
            "recipient": recipient,
            "content": dict(content),
            "approval_request_id": request_id,
        }
        self._graph.invoke(state, _thread(request_id))
        return request_id

    def resume(self, approval_request_id: str) -> dict[str, Any] | None:
        """Continue the thread waiting on this request. Returns None if no thread is waiting
        (the request was not created by a workflow, or its thread already finished)."""
        config = _thread(approval_request_id)
        if not self._graph.get_state(config).next:
            return None
        return self._graph.invoke(Command(resume=True), config)
