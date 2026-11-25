"""Branded client submission package as a PDF or Word document (BRD Agent 11, plan section 10).

The document is rendered only from the approved email content (its "package" JSON), so the
file the client receives always matches what the recruiter approved at the resume-submission
gate, including on a retried send (S24). Rendering is deterministic: the same content gives
the same text, and no date or other outside value is added.

Contents: header with the agency name, candidate and job; the nine BRD items (summary, key
facts with match %, availability, rate, visa and location, skill matrix, key projects, resume)
and the recruiter's notes; a confidentiality footer.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from recruiting.domain import GateType
from recruiting.outreach.service import Attachment

Format = Literal["pdf", "docx"]
MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass(frozen=True)
class Branding:
    name: str = "CBS LLC"
    color: str = "#1F3A93"  # header band and headings
    logo_path: str | None = None  # PNG or JPEG, shown in the header

    @property
    def rgb(self) -> tuple[int, int, int]:
        value = self.color.lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
            return (0x1F, 0x3A, 0x93)
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

    @property
    def usable_logo(self) -> str | None:
        """The logo path if it opens as an image; a missing or broken logo never blocks a
        submission (the renderers read the file only while building the document)."""
        if not self.logo_path:
            return None
        try:
            from PIL import Image as PILImage

            with PILImage.open(self.logo_path) as image:
                image.verify()
        except Exception:  # missing, unreadable or not an image
            return None
        return self.logo_path


def package_data(content: Mapping[str, str]) -> dict[str, Any] | None:
    raw = content.get("package")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def filename(data: Mapping[str, Any], fmt: Format, branding: Branding) -> str:
    stem = f"{data.get('job_code') or 'Job'} {data.get('candidate_name') or 'Candidate'}"
    stem = re.sub(r"[^\w .-]+", "", f"{stem} - {branding.name} submission").strip()
    return f"{re.sub(r'\\s+', ' ', stem)[:120]}.{fmt}"


FOOTER = (
    "Confidential. Submitted by {name} with the candidate's consent for this role only. "
    "Please do not forward or keep this document beyond your hiring decision."
)


def _facts(d: Mapping[str, Any]) -> list[tuple[str, str]]:
    match = d.get("match_percent")
    return [
        ("Match", f"{match}%" if match not in (None, "") else "to confirm"),
        ("Availability", d.get("availability") or "to confirm"),
        ("Rate", d.get("rate") or "to confirm"),
        ("Visa / work authorisation", d.get("visa") or "to confirm"),
        ("Location", d.get("location") or "to confirm"),
    ]


def _matrix(d: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    rows = []
    for m in d.get("skill_matrix") or []:
        if isinstance(m, dict) and m.get("skill"):
            level = "Required" if m.get("level") == "must_have" else "Preferred"
            rows.append((str(m["skill"]), level, "Yes" if m.get("has") else "No"))
    return rows


def _projects(d: Mapping[str, Any]) -> list[str]:
    value = d.get("key_projects") or []
    if isinstance(value, str):
        value = [line.strip(" -•") for line in value.splitlines()]
    return [str(p) for p in value if str(p).strip()]


# ----- PDF ----------


def render_pdf(d: Mapping[str, Any], branding: Branding) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Image,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    brand = colors.Color(*(c / 255 for c in branding.rgb))
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=13.5)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12.5, textColor=brand,
                        spaceBefore=12, spaceAfter=4)  # fmt: skip
    mono = ParagraphStyle("mono", parent=body, fontName="Helvetica", fontSize=9, leading=12,
                          alignment=TA_LEFT)  # fmt: skip

    def para(text: str, style=body) -> Paragraph:
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)

    story: list[Any] = []
    head_left = [
        Paragraph(f'<font color="white" size="15"><b>{escape(branding.name)}</b></font>', body),
        Paragraph('<font color="white" size="10">Candidate submission</font>', body),
    ]
    head_right = [
        Paragraph(f'<font color="white" size="13"><b>{escape(str(d.get("candidate_name") or ""))}'
                  "</b></font>", body),
        Paragraph(f'<font color="white" size="10">{escape(str(d.get("job_code") or ""))} · '
                  f'{escape(str(d.get("job_title") or ""))}</font>', body),
    ]  # fmt: skip
    logo_path = branding.usable_logo
    if logo_path:
        head_left.insert(
            0, Image(logo_path, width=0.9 * inch, height=0.9 * inch, kind="proportional")
        )
    header = Table([[head_left, head_right]], colWidths=[3.0 * inch, 4.0 * inch])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), brand),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))  # fmt: skip
    story += [header, Spacer(1, 10)]

    story += [para("Candidate summary", h2), para(d.get("candidate_summary") or "")]

    facts = Table([[Paragraph(f"<b>{escape(k)}</b>", body), para(v)] for k, v in _facts(d)],
                  colWidths=[2.0 * inch, 5.0 * inch])  # fmt: skip
    grid = ("GRID", (0, 0), (-1, -1), 0.4, colors.Color(0.8, 0.82, 0.86))
    facts.setStyle(TableStyle([grid, ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [para("Key facts", h2), facts]

    matrix = _matrix(d)
    if matrix:
        rows = [[Paragraph(f"<b>{h}</b>", body) for h in ("Skill", "Requirement", "Has it")]]
        rows += [[para(s), para(lvl), para(has)] for s, lvl, has in matrix]
        table = Table(rows, colWidths=[3.6 * inch, 2.0 * inch, 1.4 * inch], repeatRows=1)
        table.setStyle(TableStyle([
            grid, ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.93, 0.94, 0.97)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))  # fmt: skip
        story += [para("Skill matrix", h2), table]

    projects = _projects(d)
    if projects:
        story += [para("Key projects", h2), *(para(f"• {p}") for p in projects)]
    if d.get("notes"):
        story += [para("Recruiter notes", h2), para(d["notes"])]
    if d.get("resume"):
        story += [PageBreak(), para("Resume", h2), para(d["resume"], mono)]

    footer_text = FOOTER.format(name=branding.name)

    def on_page(canvas, doc) -> None:
        from reportlab.lib.utils import simpleSplit

        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.grey)
        # Wrap the notice to stop short of the page number on the right.
        lines = simpleSplit(footer_text, "Helvetica", 7.5, doc.width - 0.8 * inch)
        for n, line in enumerate(lines[:3]):
            canvas.drawString(doc.leftMargin, 0.55 * inch - n * 9.5, line)
        canvas.drawRightString(LETTER[0] - doc.rightMargin, 0.55 * inch, f"Page {doc.page}")
        canvas.restoreState()

    out = io.BytesIO()
    meta = f"{d.get('candidate_name') or ''} - {d.get('job_code') or ''}"
    doc = SimpleDocTemplate(out, pagesize=LETTER, leftMargin=0.75 * inch,
                            rightMargin=0.75 * inch, topMargin=0.6 * inch, bottomMargin=0.8 * inch,
                            title=f"Candidate submission: {meta}", author=branding.name,
                            invariant=1)  # fmt: skip
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return out.getvalue()


# ----- Word ----------


def render_docx(d: Mapping[str, Any], branding: Branding) -> bytes:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    brand = RGBColor(*branding.rgb)
    document = Document()
    document.core_properties.title = (
        f"Candidate submission: {d.get('candidate_name') or ''} - {d.get('job_code') or ''}"
    )
    document.core_properties.author = branding.name
    style = document.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(10.5)

    def heading(text: str, size: float = 13) -> None:
        run = document.add_paragraph().add_run(text)
        run.bold, run.font.size, run.font.color.rgb = True, Pt(size), brand

    def shade(cell, hex_color: str) -> None:
        props = cell._tc.get_or_add_tcPr()
        fill = OxmlElement("w:shd")
        fill.set(qn("w:val"), "clear")
        fill.set(qn("w:color"), "auto")
        fill.set(qn("w:fill"), hex_color)
        props.append(fill)

    section = document.sections[0]
    logo_path = branding.usable_logo
    if logo_path:
        section.header.paragraphs[0].add_run().add_picture(logo_path, height=Pt(28))
    head = section.header.add_paragraph()
    run = head.add_run(f"{branding.name} · Candidate submission")
    run.bold, run.font.color.rgb = True, brand
    foot = section.footer.paragraphs[0].add_run(FOOTER.format(name=branding.name))
    foot.font.size, foot.font.color.rgb = Pt(7.5), RGBColor(0x80, 0x80, 0x80)

    band = document.add_table(rows=1, cols=1)
    cell = band.rows[0].cells[0]
    shade(cell, branding.color.lstrip("#") if len(branding.color.lstrip("#")) == 6 else "1F3A93")
    name = cell.paragraphs[0].add_run(str(d.get("candidate_name") or ""))
    name.bold, name.font.size, name.font.color.rgb = True, Pt(16), RGBColor(255, 255, 255)
    role = cell.add_paragraph().add_run(f"{d.get('job_code') or ''} · {d.get('job_title') or ''}")
    role.font.size, role.font.color.rgb = Pt(11), RGBColor(255, 255, 255)

    heading("Candidate summary")
    document.add_paragraph(str(d.get("candidate_summary") or ""))

    heading("Key facts")
    facts = document.add_table(rows=0, cols=2)
    facts.style, facts.alignment = "Table Grid", WD_TABLE_ALIGNMENT.LEFT
    for key, value in _facts(d):
        row = facts.add_row().cells
        row[0].paragraphs[0].add_run(key).bold = True
        row[1].text = str(value)

    matrix = _matrix(d)
    if matrix:
        heading("Skill matrix")
        table = document.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        titles = ("Skill", "Requirement", "Has it")
        for cell, title in zip(table.rows[0].cells, titles, strict=True):
            cell.paragraphs[0].add_run(title).bold = True
            shade(cell, "EDEFF7")
        for skill, level, has in matrix:
            cells = table.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = skill, level, has

    projects = _projects(d)
    if projects:
        heading("Key projects")
        for p in projects:
            document.add_paragraph(p, style="List Bullet")
    if d.get("notes"):
        heading("Recruiter notes")
        document.add_paragraph(str(d["notes"]))
    if d.get("resume"):
        document.add_page_break()
        heading("Resume")
        for line in str(d["resume"]).splitlines():
            document.add_paragraph(line)

    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def render(content: Mapping[str, str], fmt: Format, branding: Branding) -> Attachment | None:
    data = package_data(content)
    if data is None:
        return None
    blob = render_pdf(data, branding) if fmt == "pdf" else render_docx(data, branding)
    return Attachment(filename(data, fmt, branding), MIME[fmt], blob)


class AttachmentError(Exception):
    """An approved attachment cannot be produced exactly as approved: nothing is sent."""


class SubmissionPackageRenderer:
    """Outreach Service hook: attaches the package to resume-submission emails (`fmt` None: no
    package), and the candidate's original resume when the approved content names one."""

    def __init__(self, fmt: Format | None, branding: Branding, documents: Any = None) -> None:
        self.fmt, self.branding = fmt, branding
        self.documents = documents  # storage.DocumentStore

    def __call__(self, gate: GateType, content: Mapping[str, str]) -> list[Attachment]:
        if gate is not GateType.RESUME_SUBMISSION:
            return []
        package = render(content, self.fmt, self.branding) if self.fmt else None
        return [a for a in (package, self._original(content)) if a]

    def _original(self, content: Mapping[str, str]) -> Attachment | None:
        if not content.get("resume_file"):
            return None
        ref = json.loads(content["resume_file"])
        if self.documents is None:
            raise AttachmentError("The approved resume file cannot be read: no document store")
        from recruiting.storage import StorageError

        try:
            data = self.documents.get(ref["key"])
        except StorageError as exc:
            raise AttachmentError(
                "The approved resume file is no longer stored (erased or removed)"
            ) from exc
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise AttachmentError("The stored resume file differs from the approved one")
        return Attachment(ref["filename"], ref["content_type"], data)
