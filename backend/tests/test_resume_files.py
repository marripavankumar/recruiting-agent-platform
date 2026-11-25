"""Original resume files (BRD section 11 document storage): kept as uploaded, downloadable by the
roles that see the candidate, attached to the client email exactly as approved, and deleted
with the candidate."""

import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient
from test_documents import make_docx, make_pdf  # noqa: E402

from recruiting.agents.resume_intake import ResumeFile, ResumeIntake
from recruiting.domain import GateType
from recruiting.outreach.packages import AttachmentError, Branding, SubmissionPackageRenderer
from recruiting.records.memory import InMemoryCandidates
from recruiting.storage import InMemoryDocumentStore, StorageError, safe_filename, store_resume

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
RESUME_DOCX = make_docx(header="Priya Natarajan | priya@mail.example | (313) 555-0108",
                        paragraphs=["Quality Engineer, 8 years: PFMEA, PPAP, APQP. Troy, MI"])  # fmt: skip
RESUME_PDF = make_pdf(["Priya Natarajan", "priya@mail.example | (313) 555-0108",
                       "Senior Quality Engineer, 9 years: PFMEA, PPAP, APQP, 8D."])  # fmt: skip


def test_store_keeps_the_real_type_and_a_safe_name():
    store = InMemoryDocumentStore()
    stored = store_resume(store, "cand-1", "C:\\Users\\me\\Priya CV.pdf", RESUME_PDF, "pdf")
    assert stored.key.startswith("candidates/cand-1/") and stored.key.endswith(".pdf")
    assert "Priya" not in stored.key  # keys carry no personal data
    assert stored.filename == "Priya CV.pdf" and stored.content_type == "application/pdf"
    assert stored.sha256 == hashlib.sha256(RESUME_PDF).hexdigest()
    assert store.get(stored.key) == RESUME_PDF
    # The type comes from the content: a Word file named .pdf is stored and named as .docx.
    renamed = store_resume(store, "cand-1", "resume.pdf", RESUME_DOCX, "docx")
    assert renamed.filename == "resume.docx" and renamed.content_type == DOCX
    assert safe_filename('../../etc/"passwd"') == "passwd" and safe_filename("") == "resume"
    assert store.delete_prefix("candidates/cand-1/") == 2
    with pytest.raises(StorageError):
        store.get(stored.key)


def test_a_newer_file_replaces_the_old_one_and_a_storage_failure_keeps_the_text():
    store, candidates, errors = InMemoryDocumentStore(), InMemoryCandidates(), []
    intake = ResumeIntake(candidates, documents=store, on_error=lambda k, e: errors.append(k))
    first = intake.add("Priya Natarajan\npriya@mail.example\nQuality Engineer", "manual",
                       file=ResumeFile("priya.docx", RESUME_DOCX, "docx")).candidate  # fmt: skip
    old_key = first.resume_file["key"]
    again = intake.add("Priya Natarajan\npriya@mail.example\nSenior Quality Engineer", "manual",
                       file=ResumeFile("priya-2026.pdf", RESUME_PDF, "pdf")).candidate  # fmt: skip
    assert again.id == first.id and again.resume_file["filename"] == "priya-2026.pdf"
    assert list(store.files) == [again.resume_file["key"]] and old_key not in store.files

    class Broken(InMemoryDocumentStore):
        def put(self, key, data, content_type):
            raise ConnectionError("store down")

    intake = ResumeIntake(InMemoryCandidates(), documents=Broken(),
                          on_error=lambda k, e: errors.append(k))  # fmt: skip
    kept = intake.add("Wei Zhang\nwei@mail.example\nQuality Engineer", "manual",
                      file=ResumeFile("wei.pdf", RESUME_PDF, "pdf")).candidate  # fmt: skip
    assert kept.resume_text and kept.resume_file is None and errors == ["resume_file"]


def _client():
    from recruiting.api.app import create_app
    from recruiting.api.services import build_services
    from recruiting.config import Settings

    services = build_services(Settings(demo_data=True, _env_file=None))
    return TestClient(create_app(services)), services


REC = {"X-Dev-User-Email": "recruiter@cbs.example"}
ADMIN = {"X-Dev-User-Email": "administrator@cbs.example"}
BD = {"X-Dev-User-Email": "bd.manager@cbs.example"}


def test_uploaded_files_are_kept_downloadable_and_erased_with_the_candidate():
    client, services = _client()
    files = [("files", ("José Ruiz résumé.pdf", make_pdf(["Jose Ruiz", "jose@mail.example",
                        "Controls Engineer, 6 years: PLC, Siemens S7, HMI."]), "application/pdf"))]  # fmt: skip
    result = client.post("/candidates/upload", files=files, headers=REC).json()
    cid = result["candidates"][0]["id"]
    detail = client.get(f"/candidates/{cid}", headers=REC).json()
    assert detail["resume_file"]["filename"] == "José Ruiz résumé.pdf"

    download = client.get(f"/candidates/{cid}/resume-file", headers=REC)
    assert download.status_code == 200 and download.content == files[0][1][1]
    assert download.headers["content-type"] == "application/pdf"
    disposition = download.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="Jos_ Ruiz r_sum_.pdf"')
    assert "filename*=UTF-8''Jos%C3%A9%20Ruiz%20r%C3%A9sum%C3%A9.pdf" in disposition
    assert client.get(f"/candidates/{cid}/resume-file", headers=BD).status_code == 403

    seeded = next(c for c in client.get("/candidates", headers=REC).json() if c["id"] != cid)
    assert client.get(f"/candidates/{seeded['id']}/resume-file", headers=REC).status_code == 404

    erased = client.post(f"/candidates/{cid}/erase", json={"reason": "Candidate asked"},
                         headers=ADMIN).json()  # fmt: skip
    assert erased["files_deleted"] == 1
    assert not any(k.startswith(f"candidates/{cid}/") for k in services.platform.documents.files)
    assert client.get(f"/candidates/{cid}/resume-file", headers=REC).status_code == 404


def test_the_approved_email_names_the_file_and_attaches_exactly_that_file():
    _, services = _client()
    platform = services.platform
    candidate = platform.resume_intake.add(
        "Priya Natarajan\npriya@mail.example\nQuality Engineer", "manual",
        file=ResumeFile("Priya Natarajan.docx", RESUME_DOCX, "docx"),
    ).candidate  # fmt: skip
    from recruiting.records.models import Submission

    package = {
        "candidate_name": "Priya Natarajan", "job_code": "JOB-1", "job_title": "Quality Engineer",
        "candidate_summary": "Eight years in PFMEA and PPAP.", "resume": candidate.resume_text,
        "skill_matrix": [], "match_percent": 88, "key_projects": [], "notes": "",
        "resume_file": candidate.resume_file,
    }  # fmt: skip
    content = platform.submissions.cover_email(
        Submission("s1", "j1", candidate.id, "co1", package=package), "Rae Recruiter"
    )
    assert "original resume (Priya Natarajan.docx) is also attached" in content["body"]
    ref = json.loads(content["resume_file"])
    assert ref["sha256"] == hashlib.sha256(RESUME_DOCX).hexdigest()

    renderer = SubmissionPackageRenderer("pdf", Branding(), platform.documents)
    names = [a.filename for a in renderer(GateType.RESUME_SUBMISSION, content)]
    assert len(names) == 2 and names[1] == "Priya Natarajan.docx"
    [original] = SubmissionPackageRenderer(None, Branding(), platform.documents)(
        GateType.RESUME_SUBMISSION, content
    )
    assert original.data == RESUME_DOCX and original.content_type == DOCX
    assert SubmissionPackageRenderer(None, Branding(), platform.documents)(
        GateType.FIRST_CLIENT_EMAIL, content
    ) == []  # fmt: skip

    # Not exactly the approved file: nothing is sent.
    platform.documents.files[ref["key"]] = (b"tampered", DOCX)
    with pytest.raises(AttachmentError, match="differs"):
        renderer(GateType.RESUME_SUBMISSION, content)
    platform.documents.delete(ref["key"])
    with pytest.raises(AttachmentError, match="no longer stored"):
        renderer(GateType.RESUME_SUBMISSION, content)
    with pytest.raises(AttachmentError, match="no document store"):
        SubmissionPackageRenderer("pdf", Branding())(GateType.RESUME_SUBMISSION, content)


S3_URL = os.environ.get("RAP_TEST_S3_URL")  # e.g. http://localhost:8333 (the stack's SeaweedFS)


@pytest.mark.skipif(not S3_URL, reason="RAP_TEST_S3_URL not set")
def test_s3_store_against_a_real_s3_compatible_server():
    import uuid

    from recruiting.storage import S3DocumentStore

    store = S3DocumentStore(
        f"rap-test-{uuid.uuid4().hex[:8]}", endpoint_url=S3_URL, region="us-east-1",
        access_key_id=os.environ.get("RAP_TEST_S3_KEY", "rap"),
        secret_access_key=os.environ.get("RAP_TEST_S3_SECRET", "rap-local-documents-secret"),
    )  # fmt: skip
    stored = store_resume(store, "cand-9", "cv.pdf", RESUME_PDF, "pdf")  # creates the bucket
    store_resume(store, "cand-9", "cv2.docx", RESUME_DOCX, "docx")
    assert store.get(stored.key) == RESUME_PDF
    assert store.delete_prefix("candidates/cand-9/") == 2
    with pytest.raises(StorageError):
        store.get(stored.key)
