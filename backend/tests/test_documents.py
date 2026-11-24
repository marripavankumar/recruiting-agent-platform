"""PDF and Word documents: JDs attached to client replies and resumes from candidates and
recruiters (plan sections 7 and 9)."""

import base64
import io
import re
import zipfile

import pytest
from docx import Document
from pypdf import PdfWriter

from recruiting.documents import MAX_BYTES, DocumentError, extract_text


def make_pdf(lines: list[str]) -> bytes:
    """A real one-page PDF with a text layer (Helvetica), built by hand."""
    text = " ".join(f"({line.replace('(', '[').replace(')', ']')}) Tj 0 -16 Td" for line in lines)
    stream = f"BT /F1 11 Tf 50 760 Td {text} ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for n, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % n + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objects) + 1, xref))  # fmt: skip
    return out.getvalue()


def make_docx(header="", paragraphs=(), table=()) -> bytes:
    document = Document()
    if header:
        document.sections[0].header.paragraphs[0].text = header
    for p in paragraphs:
        document.add_paragraph(p)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


JD_LINES = [
    "Job description: Quality Engineer",
    "Required: PFMEA, PPAP, APQP. 5+ years of automotive quality experience.",
    "Location: Troy, MI.",
]


def test_pdf_text_is_extracted():
    doc = extract_text("jd.pdf", make_pdf(JD_LINES))
    assert doc.kind == "pdf" and doc.pages == 1
    assert "PFMEA, PPAP, APQP" in doc.text and "Troy, MI" in doc.text


def test_docx_reads_header_body_and_tables():
    data = make_docx(
        header="Priya Natarajan | priya@mail.example | (313) 555-0108",
        paragraphs=["Quality Engineer with 8 years of experience."],
        table=[("Skills", "PFMEA, PPAP"), ("Tools", "Minitab")],
    )
    doc = extract_text("resume.docx", data)
    assert doc.kind == "docx"
    assert doc.text.startswith("Priya Natarajan | priya@mail.example")
    assert "Skills | PFMEA, PPAP" in doc.text and "Minitab" in doc.text


def test_type_comes_from_content_not_the_name():
    assert extract_text("resume.txt", make_docx(paragraphs=["Hello world"])).kind == "docx"
    assert extract_text("notes.pdf", "Plain text résumé".encode("cp1252")).kind == "text"


@pytest.mark.parametrize(
    "data, message",
    [
        (b"\xd0\xcf\x11\xe0" + b"\0" * 100, "old Word (.doc)"),
        (b"{\\rtf1 hello}", "RTF"),
        (b"%PDF-1.4 garbage", "could not be read"),
        (b"", "empty"),
        (b"\x00\x01\x02" * 50, "not a PDF, Word or text"),
    ],
)
def test_unreadable_files_are_refused_with_a_clear_message(data, message):
    with pytest.raises(DocumentError, match=re.escape(message)):
        extract_text("upload", data)


def test_scanned_encrypted_and_oversized_files():
    blank = PdfWriter()
    blank.add_blank_page(612, 792)
    out = io.BytesIO()
    blank.write(out)
    with pytest.raises(DocumentError, match="scanned image"):
        extract_text("scan.pdf", out.getvalue())

    locked = PdfWriter()
    locked.add_blank_page(612, 792)
    locked.encrypt("secret")
    out = io.BytesIO()
    locked.write(out)
    with pytest.raises(DocumentError, match="password-protected"):
        extract_text("locked.pdf", out.getvalue())

    with pytest.raises(DocumentError, match="larger than 10 MB"):
        extract_text("big.pdf", b"%PDF-" + b"0" * MAX_BYTES)

    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (51 * 1024 * 1024))
    with pytest.raises(DocumentError, match="too large"):
        extract_text("bomb.docx", bomb.getvalue())

    other_zip = io.BytesIO()
    with zipfile.ZipFile(other_zip, "w") as z:
        z.writestr("readme.txt", "hi")
    with pytest.raises(DocumentError, match=r"Word \(.docx\)"):
        extract_text("archive.zip", other_zip.getvalue())


# ----- through the API ----------

pytest.importorskip("langgraph")

from test_phase5_engagement import (  # noqa: E402
    approve,
    as_rec,
    contacted_by_email,
)
from test_phase5_engagement import ctx as engagement_ctx  # noqa: E402,F401
from test_phase8_end_to_end import BD, CSV, HOOK, REC, build, h, ok  # noqa: E402
from test_phase8_end_to_end import approve as approve_as  # noqa: E402


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_jd_attached_as_pdf_becomes_a_job_and_bad_files_are_reported():
    client, _, email, _ = build()
    ok(client.post("/companies/import", json={"source_name": "list", "csv": CSV}, headers=h(BD)))
    ok(client.post("/companies/hiring-signals", json={}, headers=h(BD)))
    [first] = ok(client.post("/companies/outreach-drafts", json={}, headers=h(BD)))["drafted"]
    approve_as(client, first["approval_request_id"], BD)
    reply = {
        "from_address": "r.lee@northfield-axle.example", "subject": "Re: roles",
        "body": "JD attached.", "message_id": "<jd-pdf@mail>",
        "attachments": [{"filename": "QE.pdf", "content_base64": b64(make_pdf(JD_LINES))},
                        {"filename": "old.doc", "content_base64": b64(b"\xd0\xcf\x11\xe0" + b"\0" * 64)},
                        {"filename": "junk.bin", "content_base64": "@@not-base64@@"}],
    }  # fmt: skip
    result = ok(client.post("/inbound/email", json=reply, headers=HOOK))
    assert result["category"] == "jd_received" and len(result["job_ids"]) == 1
    job = ok(client.get(f"/jobs/{result['job_ids'][0]}", headers=h(REC)))
    assert {"PFMEA", "PPAP", "APQP"} <= set(job["analysis"]["must_have_skills"])
    errors = {e["filename"]: e["error"] for e in result["attachment_errors"]}
    assert (
        "old Word (.doc)" in errors["old.doc"] and errors["junk.bin"] == "Not valid base64 content."
    )


def test_recruiter_uploads_resume_files():
    client, services, _, _ = build()
    files = [
        ("files", ("priya.docx", make_docx(
            header="Priya Natarajan | priya@mail.example | (313) 555-0108",
            paragraphs=["Quality Engineer, 8 years: PFMEA, PPAP, APQP and 8D. Troy, MI"]),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
        ("files", ("wei.pdf", make_pdf(["Wei Zhang", "wei@mail.example | (313) 555-0106",
                                         "Quality Engineer with 6 years in PPAP and APQP."]),
                   "application/pdf")),
        ("files", ("scan.pdf", b"%PDF-1.4 broken", "application/pdf")),
    ]  # fmt: skip
    result = ok(client.post("/candidates/upload", files=files, data={"source": "ats"},
                            headers=h(REC)))  # fmt: skip
    assert result["created"] == 2 and [e["filename"] for e in result["errors"]] == ["scan.pdf"]
    names = {c["full_name"] for c in result["candidates"]}
    assert names == {"Priya Natarajan", "Wei Zhang"}
    priya = next(c for c in result["candidates"] if c["full_name"] == "Priya Natarajan")
    assert "PFMEA" in services.platform.candidates.get(priya["id"]).skills
    assert client.post("/candidates/upload", files=files[:1], headers=h(BD)).status_code == 403


def test_candidate_uploads_a_resume_file_on_the_secure_link(engagement_ctx):  # noqa: F811
    client, services, email = engagement_ctx
    engagement = contacted_by_email(client, "c-john")
    out = client.post(f"/engagements/{engagement['id']}/outcome", json={"outcome": "interested"},
                      headers=as_rec()).json()  # fmt: skip
    approve(client, out["approvals"]["details_request"])
    token = out["upload_link"].rsplit("/", 1)[1]
    assert 'type="file"' in client.get(f"/collect/{token}").text

    details = {"availability": "2 weeks", "visa_status": "Green card",
               "current_location": "Auburn Hills, MI", "expected_rate": "$80/h",
               "notice_period": "2 weeks", "work_authorization": "Green card holder",
               "preferred_location": "Troy, MI", "consent_store": "yes", "consent_submit": "yes"}  # fmt: skip
    bad = client.post(f"/collect/{token}", data=details,
                      files={"resume_file": ("resume.doc", b"\xd0\xcf\x11\xe0" + b"\0" * 64)})  # fmt: skip
    assert "could not read your file" in bad.text and "old Word (.doc)" in bad.text
    assert "We still need" in bad.text  # the other details were kept; only the resume is missing

    resume = make_docx(header="John Mercer | john@mail.example",
                       paragraphs=["Crash CAE engineer, 7 years: LS-DYNA, ANSA, FMVSS."])  # fmt: skip
    good = client.post(f"/collect/{token}", data={"consent_store": "yes", "consent_submit": "yes"},
                       files={"resume_file": ("john.docx", resume)})  # fmt: skip
    assert "has everything needed" in good.text
    assert "FMVSS" in services.platform.candidates.get("c-john").skills
