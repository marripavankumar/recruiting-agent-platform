"""Text from uploaded documents: JDs attached to client replies (Agent 4) and resumes from
candidates and recruiters (Agents 6 and 9). Plan sections 7 and 9.

PDF (text layer) and Word .docx are read; plain text is passed through. The file type is
decided from the content, not the file name. Limits guard against oversized or malicious
files: 10 MB per file, 50 PDF pages, 50 MB uncompressed inside a .docx, 200,000 characters of
text. Scanned PDFs (no text layer), encrypted PDFs and old binary .doc files are refused with
a message the uploader can act on; OCR is not done here.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

MAX_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_DOCX_UNCOMPRESSED = 50 * 1024 * 1024
MAX_CHARS = 200_000
MIN_CHARS = 20  # less than this from a PDF means it is a scanned image


class DocumentError(ValueError):
    """The file cannot be read; the message is safe to show to the person who sent it."""


@dataclass(frozen=True)
class ExtractedDocument:
    filename: str
    kind: str  # "pdf", "docx" or "text"
    text: str
    pages: int | None = None
    truncated: bool = False


def _clean(text: str) -> tuple[str, bool]:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return (text[:MAX_CHARS], True) if len(text) > MAX_CHARS else (text, False)


def _kind(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"  # Word 97-2003 .doc
    if data.startswith(b"{\\rtf"):
        return "rtf"
    return "text"


def _pdf(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("The PDF is password-protected. Please send it without a password.")
        pages = len(reader.pages)
        if pages > MAX_PDF_PAGES:
            raise DocumentError(f"The PDF has {pages} pages; the limit is {MAX_PDF_PAGES}.")
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise DocumentError("The PDF could not be read; it may be damaged.") from exc
    if len(text.strip()) < MIN_CHARS:
        raise DocumentError(
            "The PDF looks like a scanned image with no text in it. Please send a Word or "
            "text version, or paste the text."
        )
    return text, pages


def _docx(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = archive.namelist()
        if "word/document.xml" not in names:
            raise DocumentError("Only Word (.docx) documents are accepted from zip files.")
        if sum(info.file_size for info in archive.infolist()) > MAX_DOCX_UNCOMPRESSED:
            raise DocumentError("The Word document is too large to read.")
    except zipfile.BadZipFile as exc:
        raise DocumentError("The Word document could not be read; it may be damaged.") from exc

    from docx import Document

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several unrelated types on bad input
        raise DocumentError("The Word document could not be read; it may be damaged.") from exc
    parts: list[str] = []
    # Resumes often put the name and contact line in the page header.
    for section in document.sections:
        parts += [p.text for p in section.header.paragraphs if p.text.strip()]
    parts += [p.text for p in document.paragraphs]
    # Skills and work history are often laid out in tables.
    for table in document.tables:
        for row in table.rows:
            cells = list(dict.fromkeys(c.text.strip() for c in row.cells if c.text.strip()))
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def extract_text(filename: str, data: bytes) -> ExtractedDocument:
    name = (filename or "attachment").strip()[:200]
    if not data:
        raise DocumentError(f"{name} is empty.")
    if len(data) > MAX_BYTES:
        raise DocumentError(f"{name} is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    kind = _kind(data)
    pages = None
    if kind == "pdf":
        raw, pages = _pdf(data)
    elif kind == "zip":
        raw, kind = _docx(data), "docx"
    elif kind == "ole":
        raise DocumentError(
            f"{name} is an old Word (.doc) file. Please save it as .docx or PDF and send it again."
        )
    elif kind == "rtf":
        raise DocumentError(f"{name} is an RTF file. Please send it as .docx, PDF or text.")
    else:
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                raw = data.decode("cp1252")
            except UnicodeDecodeError as exc:
                raise DocumentError(
                    f"{name} is not a PDF, Word or text document we can read."
                ) from exc
        if "\x00" in raw[:1000]:
            raise DocumentError(f"{name} is not a PDF, Word or text document we can read.")
    text, truncated = _clean(raw)
    if not text:
        raise DocumentError(f"No text was found in {name}.")
    return ExtractedDocument(name, kind, text, pages, truncated)
