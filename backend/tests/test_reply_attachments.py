"""Resumes attached to a candidate's email reply are kept as the original file (BRD section 11),
like uploads: the attachment that reads like a resume is chosen, a cover letter is not mixed
in, and unreadable files are reported."""

import base64

from fastapi.testclient import TestClient
from test_documents import make_docx, make_pdf  # noqa: E402

from recruiting.agents.jd_intake import InboundFile, read_attachments
from recruiting.agents.resume_collection import pick_resume
from recruiting.api.app import create_app
from recruiting.api.services import build_services
from recruiting.config import Settings
from recruiting.demo import WEBHOOK_SECRET

RESUME = make_pdf(["Senior Quality Engineer, 9 years", "PFMEA, PPAP, APQP, 8D, SPC, MSA",
                   "Supplier audits at a Tier 1 automotive supplier, Troy MI"])  # fmt: skip
COVER = make_docx(paragraphs=["Dear hiring team, thank you for reaching out.",
                              "My resume is attached. I look forward to hearing from you."])  # fmt: skip
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452")  # a real image header


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_the_resume_is_picked_over_a_cover_letter():
    files, errors = read_attachments([("cover.docx", COVER), ("cv.pdf", RESUME),
                                      ("photo.png", PNG)])  # fmt: skip
    assert [f.filename for f in files] == ["cover.docx", "cv.pdf"]
    assert [e["filename"] for e in errors] == ["photo.png"]
    assert pick_resume(files).filename == "cv.pdf"
    assert pick_resume(()) is None
    assert pick_resume((InboundFile("blank.txt", b" ", "text", " "),)) is None
    assert "data=" not in repr(files[1]) and "text=" not in repr(files[1])  # no content in logs


def test_a_reply_with_a_resume_keeps_the_original_file():
    services = build_services(Settings(demo_data=True, _env_file=None))
    client = TestClient(create_app(services))
    platform = services.platform
    contacted = next(
        c for c in platform.candidates.list() if platform.engagements.for_candidate(c.id)
    )
    assert contacted.resume_file is None

    reply = {
        "from_address": contacted.email, "subject": "Re: Quality Engineer opportunity",
        "body": "Hi, my resume is attached as requested.", "message_id": "<cv-reply-1@mail>",
        "attachments": [
            {"filename": "Cover letter.docx", "content_base64": b64(COVER)},
            {"filename": "My CV.pdf", "content_base64": b64(RESUME)},
            {"filename": "scan.png", "content_base64": b64(PNG)},
        ],
    }  # fmt: skip
    response = client.post("/inbound/email", json=reply,
                           headers={"X-RAP-Webhook-Secret": WEBHOOK_SECRET})  # fmt: skip
    assert response.status_code == 200, response.text
    assert [e["filename"] for e in response.json()["attachment_errors"]] == ["scan.png"]

    candidate = platform.candidates.get(contacted.id)
    assert candidate.resume_file["filename"] == "My CV.pdf"
    assert candidate.resume_file["content_type"] == "application/pdf"
    assert platform.documents.get(candidate.resume_file["key"]) == RESUME
    assert "Supplier audits" in candidate.resume_text
    assert "Dear hiring team" not in candidate.resume_text  # the cover letter is not the resume

    # Recruiters download it like an upload.
    download = client.get(f"/candidates/{candidate.id}/resume-file",
                          headers={"X-Dev-User-Email": "recruiter@cbs.example"})  # fmt: skip
    assert download.status_code == 200 and download.content == RESUME

    # A later reply with a new resume replaces the file; the old one is deleted.
    old_key = candidate.resume_file["key"]
    newer = make_pdf(["Senior Quality Engineer, 10 years", "PFMEA, PPAP, APQP, 8D, SPC, MSA, GD&T"])
    client.post("/inbound/email", headers={"X-RAP-Webhook-Secret": WEBHOOK_SECRET}, json={
        **reply, "message_id": "<cv-reply-2@mail>",
        "attachments": [{"filename": "CV 2026.pdf", "content_base64": b64(newer)}],
    })  # fmt: skip
    candidate = platform.candidates.get(contacted.id)
    assert candidate.resume_file["filename"] == "CV 2026.pdf"
    assert old_key not in platform.documents.files
