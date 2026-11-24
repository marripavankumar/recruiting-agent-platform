"""Branded client submission package (Agent 11): PDF and Word, rendered only from the approved
content and attached to the submission email."""

import email
import io
import json

import httpx
import pytest
from docx import Document
from PIL import Image

from recruiting.approvals import ApprovalEngine, DecisionAction, Reviewer
from recruiting.compliance import OptOutRegistry
from recruiting.documents import extract_text
from recruiting.domain import Channel, GateType, Role
from recruiting.outreach import (
    Attachment,
    DeliveryResult,
    DeliveryStatus,
    OutboundMessage,
    OutreachService,
)
from recruiting.outreach.channels import GmailEmailChannel, Microsoft365EmailChannel
from recruiting.outreach.packages import (
    Branding,
    SubmissionPackageRenderer,
    filename,
    render,
)

PACKAGE = {
    "candidate_name": "Priya Natarajan",
    "job_code": "JOB-2026-0142",
    "job_title": "CAE Engineer - Crash",
    "candidate_summary": "Crash CAE engineer with 8 years in LS-DYNA & ANSA <frontal, side>.",
    "resume": "Priya Natarajan\nLead crash engineer at Lakeside Automotive.\nLS-DYNA, ANSA",
    "skill_matrix": [{"skill": "LS-DYNA", "level": "must_have", "has": True},
                     {"skill": "Python", "level": "nice_to_have", "has": False}],
    "match_percent": 92,
    "availability": "2 weeks",
    "rate": "$80/hr C2C",
    "visa": "Green card",
    "location": "Auburn Hills, MI",
    "key_projects": ["Frontal crash model for a 2027 SUV", "IIHS side-impact correlation"],
    "notes": "Led two FMVSS 208 programmes.",
}  # fmt: skip
CONTENT = {"subject": "Candidate submission", "body": "Hello", "package": json.dumps(PACKAGE)}


def test_pdf_has_every_brd_item_and_is_deterministic():
    doc = render(CONTENT, "pdf", Branding())
    assert doc.content_type == "application/pdf"
    assert doc.filename == "JOB-2026-0142 Priya Natarajan - CBS LLC submission.pdf"
    text = extract_text(doc.filename, doc.data).text
    for expected in ("CBS LLC", "Candidate submission", "Priya Natarajan", "CAE Engineer - Crash",
                     "Crash CAE engineer", "<frontal, side>", "92%", "2 weeks", "$80/hr C2C",
                     "Green card", "Auburn Hills, MI", "LS-DYNA", "Required", "Preferred",
                     "Frontal crash model", "Led two FMVSS 208", "Lakeside Automotive",
                     "Confidential", "Page 2"):  # fmt: skip
        assert expected in text, expected
    assert render(CONTENT, "pdf", Branding()).data == doc.data  # same approval, same file


def test_docx_has_every_brd_item():
    doc = render(CONTENT, "docx", Branding(name="Acme Staffing", color="#AA3300"))
    assert doc.filename.endswith("Acme Staffing submission.docx")
    document = Document(io.BytesIO(doc.data))
    body = "\n".join(p.text for p in document.paragraphs)
    cells = "\n".join(c.text for t in document.tables for r in t.rows for c in r.cells)
    header = document.sections[0].header.paragraphs[-1].text
    assert header == "Acme Staffing · Candidate submission"
    assert "Priya Natarajan" in cells and "$80/hr C2C" in cells and "LS-DYNA" in cells
    assert "Frontal crash model" in body and "Lakeside Automotive" in body
    assert "Confidential" in document.sections[0].footer.paragraphs[0].text


def test_logo_bad_colour_and_odd_names(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGB", (120, 60), (31, 58, 147)).save(logo)
    for fmt in ("pdf", "docx"):
        assert render(CONTENT, fmt, Branding(logo_path=str(logo))).data
        assert render(CONTENT, fmt, Branding(logo_path=str(tmp_path / "missing.png"))).data
    assert Branding(color="not-a-colour").rgb == (0x1F, 0x3A, 0x93)
    odd = {"candidate_name": 'Ann "AJ" O/Brien', "job_code": "JOB:1"}
    assert filename(odd, "pdf", Branding()) == "JOB1 Ann AJ OBrien - CBS LLC submission.pdf"
    assert render({"subject": "x", "body": "y"}, "pdf", Branding()) is None
    assert render({"package": "not json"}, "pdf", Branding()) is None


def test_only_resume_submissions_get_the_package():
    renderer = SubmissionPackageRenderer("pdf", Branding())
    assert len(renderer(GateType.RESUME_SUBMISSION, CONTENT)) == 1
    assert renderer(GateType.FIRST_CLIENT_EMAIL, CONTENT) == []
    assert renderer(GateType.RESUME_SUBMISSION, {"body": "no package"}) == []


class Capture:
    channel = Channel.EMAIL

    def __init__(self):
        self.sent = []

    def deliver(self, message):
        self.sent.append(message)
        return DeliveryResult(DeliveryStatus.SENT, "m-1")


def approved(engine, gate=GateType.RESUME_SUBMISSION):
    request = engine.submit(gate, "sub-1", CONTENT, requested_by="agent-11")
    engine.decide(request.id, Reviewer("u-rec", Role.RECRUITER), DecisionAction.APPROVE)
    return OutboundMessage(request.id, Channel.EMAIL, "hm@client.example", CONTENT)


def test_outreach_service_attaches_the_package_after_the_approval_check():
    engine, capture = ApprovalEngine(), Capture()
    service = OutreachService(engine, OptOutRegistry(), [capture],
                              attachments=SubmissionPackageRenderer("pdf", Branding()))  # fmt: skip
    service.send(approved(engine))
    [sent] = capture.sent
    assert [a.filename for a in sent.attachments] == [
        "JOB-2026-0142 Priya Natarajan - CBS LLC submission.pdf"
    ]


def test_a_rendering_failure_leaves_the_approval_unused():
    def broken(gate, content):
        raise RuntimeError("font missing")

    engine, capture = ApprovalEngine(), Capture()
    service = OutreachService(engine, OptOutRegistry(), [capture], attachments=broken)
    message = approved(engine)
    with pytest.raises(RuntimeError):
        service.send(message)
    assert service.receipt(message.approval_request_id) is None and capture.sent == []


PDF = Attachment("pkg.pdf", "application/pdf", b"%PDF-1.4 test")
MESSAGE = OutboundMessage("a-1", Channel.EMAIL, "hm@client.example",
                          {"subject": "Submission", "body": "Hello"}, (PDF,))  # fmt: skip


def test_gmail_and_microsoft_365_carry_the_attachment():
    mime = GmailEmailChannel("rec@cbs.example", lambda: "t").mime(MESSAGE)
    parsed = email.message_from_bytes(mime.as_bytes())
    [part] = [p for p in parsed.walk() if p.get_filename()]
    assert part.get_filename() == "pkg.pdf" and part.get_content_type() == "application/pdf"
    assert part.get_payload(decode=True) == b"%PDF-1.4 test"

    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(202)

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://graph.test")
    Microsoft365EmailChannel("rec@cbs.example", lambda: "t", client=http).deliver(MESSAGE)
    [attachment] = seen["message"]["attachments"]
    assert attachment["name"] == "pkg.pdf" and attachment["contentBytes"] == "JVBERi0xLjQgdGVzdA=="


# ----- preview through the API ----------

pytest.importorskip("langgraph")

from test_phase6_submission import (  # noqa: E402
    AM,
    CLIENT,
    REC,
    ctx,  # noqa: E402,F401
    decide,
    h,
    prepare,
    request_approval,
)


def test_recruiter_previews_the_exact_package(ctx):  # noqa: F811
    client, services, email_channel = ctx
    sid = prepare(client, "c-priya")["id"]
    draft = client.get(f"/submissions/{sid}/package", headers=h(REC))
    assert draft.status_code == 200 and draft.headers["content-type"] == "application/pdf"
    assert (
        'filename="JOB-2026-0142 Priya Natarajan - CBS LLC submission.pdf"'
        in draft.headers["content-disposition"]
    )
    word = client.get(f"/submissions/{sid}/package", params={"format": "docx"}, headers=h(AM))
    assert word.status_code == 200 and word.content.startswith(b"PK")
    assert client.get(f"/submissions/{sid}/package", headers=h(CLIENT)).status_code == 403
    bad = client.get(f"/submissions/{sid}/package", params={"format": "exe"}, headers=h(REC))
    assert bad.status_code == 422

    sub = request_approval(client, sid).json()
    pending = client.get(f"/submissions/{sid}/package", headers=h(REC))
    decide(client, sub["approval_request_id"])
    [sent] = email_channel.sent
    # What the approver previewed is byte for byte what the client received.
    assert sent.attachments[0].data == pending.content
