"""Text extraction: turn an uploaded file's bytes into sections of plain text.

A section is a span of text with a single location (a Markdown heading or a PDF page).
Chunks never cross sections, so every chunk can be cited precisely.
"""

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PyPdfError


class ExtractionError(Exception):
    """The file could not be turned into text."""


@dataclass(frozen=True)
class Section:
    text: str
    heading: str | None = None  # Markdown heading path, e.g. "Architecture > API"
    page_number: int | None = None  # PDF page, starting at 1


def extract_sections(content: bytes, content_type: str) -> list[Section]:
    if content_type == "text/markdown":
        sections = _markdown_sections(_decode(content))
    elif content_type == "text/plain":
        sections = [Section(text=_normalize(_decode(content)))]
    elif content_type == "application/pdf":
        sections = _pdf_sections(content)
    else:
        raise ExtractionError(f"No extractor for content type {content_type!r}")

    sections = [section for section in sections if section.text]
    if not sections:
        raise ExtractionError("No extractable text found")
    return sections


def _decode(content: bytes) -> str:
    try:
        # "utf-8-sig" also drops the invisible byte-order mark some Windows editors add.
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExtractionError("File is not valid UTF-8 text") from exc


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")  # PDFs sometimes contain NUL; Postgres text cannot store it
    text = re.sub(r"[ \t]+\n", "\n", text)  # trailing spaces at line ends
    text = re.sub(r"\n{3,}", "\n\n", text)  # at most one blank line in a row
    return text.strip()


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")  # "## Title" or "## Title ##"
_FENCE = re.compile(r"^\s*(```|~~~)")  # start or end of a fenced code block


def _markdown_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    heading_path: list[str] = []  # one entry per heading level currently open
    lines: list[str] = []
    in_code_block = False

    def close_section() -> None:
        heading = " > ".join(heading_path) or None
        sections.append(Section(text=_normalize("\n".join(lines)), heading=heading))
        lines.clear()

    for line in text.split("\n"):
        if _FENCE.match(line):
            in_code_block = not in_code_block
        # Inside code, "# comment" is not a heading.
        match = None if in_code_block else _HEADING.match(line)
        if match:
            close_section()
            level = len(match.group(1))
            del heading_path[level - 1 :]  # close this level and anything deeper
            heading_path.append(match.group(2))
        else:
            lines.append(line)
    close_section()
    return sections


def _pdf_sections(content: bytes) -> list[Section]:
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ExtractionError("PDF is password-protected")
        page_texts = [page.extract_text() or "" for page in reader.pages]
    except PyPdfError as exc:
        raise ExtractionError(f"Could not read PDF: {exc}") from exc

    sections = [
        Section(text=_normalize(text), page_number=number)
        for number, text in enumerate(page_texts, start=1)
    ]
    if not any(section.text for section in sections):
        raise ExtractionError(
            "PDF has no extractable text (scanned PDFs need OCR, which is not supported yet)"
        )
    return sections
