import pytest

from recruiting.approvals import ApprovalEngine, DecisionAction, NotApproved, Reviewer
from recruiting.compliance import OptOutRegistry
from recruiting.domain import Channel, GateType, Role
from recruiting.outreach import (
    AlreadySent,
    DeliveryResult,
    DeliveryStatus,
    GateChannelMismatch,
    ManualLinkedInChannel,
    NoActiveChannel,
    OutboundMessage,
    OutreachDisabled,
    OutreachService,
    RecipientOptedOut,
)

CONTENT = {"subject": "Engineering Talent Support", "body": "Dear Ms Lee, ..."}


class FakeEmailChannel:
    channel = Channel.EMAIL

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    def deliver(self, message: OutboundMessage) -> DeliveryResult:
        self.sent.append(message)
        return DeliveryResult(DeliveryStatus.SENT, provider_message_id=f"msg-{len(self.sent)}")


@pytest.fixture
def setup():
    engine = ApprovalEngine()
    opt_outs = OptOutRegistry()
    email = FakeEmailChannel()
    linkedin = ManualLinkedInChannel()
    service = OutreachService(engine, opt_outs, [email, linkedin])
    return engine, opt_outs, email, linkedin, service


def approved(engine, gate=GateType.FIRST_CLIENT_EMAIL, role=Role.BD_MANAGER):
    request = engine.submit(gate, "subject-1", CONTENT, requested_by="agent")
    engine.decide(request.id, Reviewer("u-1", role), DecisionAction.APPROVE)
    return request


def email_to(request_id, recipient="lee@example.com", content=CONTENT):
    return OutboundMessage(request_id, Channel.EMAIL, recipient, content)


def test_send_without_approval_is_blocked(setup):
    engine, _, email, _, service = setup
    request = engine.submit(GateType.FIRST_CLIENT_EMAIL, "company-1", CONTENT, requested_by="a")
    with pytest.raises(NotApproved):
        service.send(email_to(request.id))
    assert email.sent == []


def test_approved_email_is_sent_once(setup):
    engine, _, email, _, service = setup
    request = approved(engine)
    receipt = service.send(email_to(request.id))
    assert receipt.status is DeliveryStatus.SENT
    assert len(email.sent) == 1
    with pytest.raises(AlreadySent):
        service.send(email_to(request.id))


def test_changed_content_is_blocked(setup):
    engine, _, email, _, service = setup
    request = approved(engine)
    with pytest.raises(NotApproved):
        service.send(email_to(request.id, content={**CONTENT, "body": "different"}))
    assert email.sent == []


def test_opted_out_recipient_is_blocked_case_insensitively(setup):
    engine, opt_outs, email, _, service = setup
    opt_outs.record("Lee@Example.com", source="unsubscribe-link")
    request = approved(engine)
    with pytest.raises(RecipientOptedOut):
        service.send(email_to(request.id, recipient="lee@example.com"))
    assert email.sent == []


def test_email_approval_cannot_be_used_on_linkedin(setup):
    engine, _, _, linkedin, service = setup
    request = approved(engine)
    with pytest.raises(GateChannelMismatch):
        service.send(OutboundMessage(request.id, Channel.LINKEDIN, "linkedin.com/in/lee", CONTENT))
    assert linkedin.tasks == []


def test_linkedin_request_is_queued_for_manual_send(setup):
    engine, _, _, linkedin, service = setup
    request = approved(engine, GateType.LINKEDIN_CONNECTION_REQUEST, Role.RECRUITER)
    receipt = service.send(
        OutboundMessage(request.id, Channel.LINKEDIN, "https://linkedin.com/in/john/", CONTENT)
    )
    assert receipt.status is DeliveryStatus.QUEUED_FOR_MANUAL_SEND
    assert len(linkedin.tasks) == 1


def test_no_active_channel():
    engine = ApprovalEngine()
    service = OutreachService(engine, OptOutRegistry(), [])
    request = approved(engine)
    with pytest.raises(NoActiveChannel):
        service.send(email_to(request.id))


class BrokenEmailChannel:
    channel = Channel.EMAIL

    def __init__(self) -> None:
        self.calls = 0

    def deliver(self, message: OutboundMessage) -> DeliveryResult:
        self.calls += 1
        raise TimeoutError("provider did not answer")


def test_receipt_is_logged(setup):
    engine, _, _, _, service = setup
    request = approved(engine)
    service.send(email_to(request.id))
    receipt = service.receipt(request.id)
    assert receipt.status is DeliveryStatus.SENT
    assert receipt.provider_message_id == "msg-1"


def test_failed_delivery_keeps_its_claim_so_it_is_never_retried_blindly():
    engine = ApprovalEngine()
    broken = BrokenEmailChannel()
    service = OutreachService(engine, OptOutRegistry(), [broken])
    request = approved(engine)
    with pytest.raises(TimeoutError):
        service.send(email_to(request.id))
    receipt = service.receipt(request.id)
    assert receipt.status is DeliveryStatus.FAILED
    assert "provider did not answer" in receipt.error
    with pytest.raises(AlreadySent):
        service.send(email_to(request.id))
    assert broken.calls == 1


def test_disabled_outreach_sends_nothing_and_keeps_the_approval_unused():
    engine = ApprovalEngine()
    email = FakeEmailChannel()
    service = OutreachService(engine, OptOutRegistry(), [email], enabled=False)
    request = approved(engine)
    with pytest.raises(OutreachDisabled):
        service.send(email_to(request.id))
    assert email.sent == []
    assert service.receipt(request.id) is None


def test_allow_list_limits_recipients():
    engine = ApprovalEngine()
    email = FakeEmailChannel()
    service = OutreachService(engine, OptOutRegistry(), [email], allow_list=["QA@cbs.example"])
    with pytest.raises(OutreachDisabled):
        service.send(email_to(approved(engine).id, recipient="lee@example.com"))
    service.send(email_to(approved(engine).id, recipient="qa@cbs.example"))
    assert [m.recipient for m in email.sent] == ["qa@cbs.example"]
