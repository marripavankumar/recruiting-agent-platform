import pytest

from recruiting.approvals import (
    DEFAULT_POLICIES,
    ApprovalEngine,
    ApprovalStatus,
    DecisionAction,
    GatePolicy,
    InvalidDecision,
    NotApproved,
    NotAuthorizedToApprove,
    Reviewer,
)
from recruiting.domain import GateType, Role

DRAFT = {"subject": "Engineering Talent Support for Your Hiring Needs", "body": "Dear Ms Lee, ..."}
BD = Reviewer("u-bd", Role.BD_MANAGER)
RECRUITER = Reviewer("u-rec", Role.RECRUITER)


def submit_first_email(engine: ApprovalEngine):
    return engine.submit(GateType.FIRST_CLIENT_EMAIL, "company-1", DRAFT, requested_by="agent-3")


def test_q14_default_approvers():
    assert DEFAULT_POLICIES[GateType.FIRST_CLIENT_EMAIL].first_approver is Role.BD_MANAGER
    for gate in (
        GateType.LINKEDIN_CONNECTION_REQUEST,
        GateType.CANDIDATE_OUTREACH_EMAIL,
        GateType.RESUME_SUBMISSION,
    ):
        assert DEFAULT_POLICIES[gate].first_approver is Role.RECRUITER


def test_wrong_role_cannot_approve():
    engine = ApprovalEngine()
    request = submit_first_email(engine)
    with pytest.raises(NotAuthorizedToApprove):
        engine.decide(request.id, RECRUITER, DecisionAction.APPROVE)
    assert request.status is ApprovalStatus.PENDING


def test_approve_then_send_check_passes_only_for_same_content():
    engine = ApprovalEngine()
    request = submit_first_email(engine)
    engine.decide(request.id, BD, DecisionAction.APPROVE)
    assert request.status is ApprovalStatus.APPROVED
    engine.assert_approved(request.id, DRAFT)
    with pytest.raises(NotApproved):
        engine.assert_approved(request.id, {**DRAFT, "body": "changed after approval"})


def test_pending_request_is_not_approved():
    engine = ApprovalEngine()
    request = submit_first_email(engine)
    with pytest.raises(NotApproved):
        engine.assert_approved(request.id, DRAFT)


def test_rejection_needs_reason():
    engine = ApprovalEngine()
    request = submit_first_email(engine)
    with pytest.raises(InvalidDecision):
        engine.decide(request.id, BD, DecisionAction.REJECT)
    engine.decide(request.id, BD, DecisionAction.REJECT, reason="Wrong contact")
    assert request.status is ApprovalStatus.REJECTED
    with pytest.raises(InvalidDecision):
        engine.decide(request.id, BD, DecisionAction.APPROVE)


def test_edit_and_approve_changes_approved_version():
    engine = ApprovalEngine()
    request = submit_first_email(engine)
    edited = {**DRAFT, "body": "Dear Ms Lee, edited"}
    engine.decide(request.id, BD, DecisionAction.EDIT_AND_APPROVE, edited_content=edited)
    engine.assert_approved(request.id, edited)
    with pytest.raises(NotApproved):
        engine.assert_approved(request.id, DRAFT)


def test_second_approver_must_be_a_different_person_and_cannot_edit():
    policies = dict(DEFAULT_POLICIES)
    policies[GateType.RESUME_SUBMISSION] = GatePolicy(
        GateType.RESUME_SUBMISSION, Role.RECRUITER, Role.RECRUITER
    )
    engine = ApprovalEngine(policies)
    request = engine.submit(GateType.RESUME_SUBMISSION, "cand-1", DRAFT, requested_by="agent-11")

    engine.decide(request.id, RECRUITER, DecisionAction.APPROVE)
    assert request.status is ApprovalStatus.PENDING
    with pytest.raises(NotAuthorizedToApprove):
        engine.decide(request.id, RECRUITER, DecisionAction.APPROVE)

    second = Reviewer("u-rec-2", Role.RECRUITER)
    with pytest.raises(InvalidDecision):
        engine.decide(
            request.id, second, DecisionAction.EDIT_AND_APPROVE, edited_content={"body": "x"}
        )
    engine.decide(request.id, second, DecisionAction.APPROVE)
    assert request.status is ApprovalStatus.APPROVED


def test_requester_cannot_approve_own_request():
    engine = ApprovalEngine()
    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "company-1", DRAFT, requested_by="u-bd")
    with pytest.raises(NotAuthorizedToApprove):
        engine.decide(request.id, BD, DecisionAction.APPROVE)


def test_pending_queue_by_role():
    engine = ApprovalEngine()
    request = submit_first_email(engine)
    assert engine.pending_for(Role.BD_MANAGER) == [request]
    assert engine.pending_for(Role.RECRUITER) == []


def test_submit_with_request_id_is_idempotent():
    engine = ApprovalEngine()
    first = engine.submit(
        GateType.FIRST_CLIENT_EMAIL, "c-1", {"body": "a"}, requested_by="agent", request_id="r-1"
    )
    again = engine.submit(
        GateType.FIRST_CLIENT_EMAIL, "c-1", {"body": "a"}, requested_by="agent", request_id="r-1"
    )
    assert first.id == again.id == "r-1"
    assert len(engine.pending_for(Role.BD_MANAGER)) == 1
