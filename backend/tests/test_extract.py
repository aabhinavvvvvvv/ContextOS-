import pytest

from app.ingestion.extract import ExtractionError, Section, extract_sections


def make_pdf(page_texts: list[str]) -> bytes:
    """Build a minimal valid PDF with one line of text per page (no parentheses in text).

    A PDF is a list of numbered objects plus an "xref" table of each object's byte offset.
    """
    page_ids = [4 + 2 * i for i in range(len(page_texts))]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_texts)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, text in zip(page_ids, page_texts):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref_offset = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        pdf += b"%010d 00000 n \n" % offset
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_offset,
    )
    return bytes(pdf)


MARKDOWN = b"""Intro before any heading.

# Architecture
We use FastAPI.

## API
REST, not GraphQL.

```bash
# this is a shell comment, not a heading
```

# Tasks
## Open
- [ ] write tests
"""


def test_markdown_splits_at_headings_and_keeps_heading_path() -> None:
    sections = extract_sections(MARKDOWN, "text/markdown")

    assert sections == [
        Section(text="Intro before any heading."),
        Section(text="We use FastAPI.", heading="Architecture"),
        Section(
            text="REST, not GraphQL.\n\n```bash\n# this is a shell comment, not a heading\n```",
            heading="Architecture > API",
        ),
        # "# Tasks" has no text of its own, so it produces no section
        Section(text="- [ ] write tests", heading="Tasks > Open"),
    ]


def test_plain_text_is_normalized() -> None:
    raw = b"\xef\xbb\xbfline one  \r\nline two\r\n\r\n\r\n\r\nnext\x00 paragraph\n"

    [section] = extract_sections(raw, "text/plain")

    assert section.text == "line one\nline two\n\nnext paragraph"


def test_invalid_utf8_raises() -> None:
    with pytest.raises(ExtractionError, match="UTF-8"):
        extract_sections(b"\xff\xfe\xfa", "text/plain")


def test_whitespace_only_file_raises() -> None:
    with pytest.raises(ExtractionError, match="No extractable text"):
        extract_sections(b"  \n\n \t ", "text/plain")


def test_pdf_produces_one_section_per_page_with_page_numbers() -> None:
    pdf = make_pdf(["Decision: use REST", "Open task: write tests"])

    sections = extract_sections(pdf, "application/pdf")

    assert [(s.page_number, s.text) for s in sections] == [
        (1, "Decision: use REST"),
        (2, "Open task: write tests"),
    ]


def test_pdf_without_text_raises_scanned_pdf_error() -> None:
    with pytest.raises(ExtractionError, match="OCR"):
        extract_sections(make_pdf([""]), "application/pdf")


def test_corrupt_pdf_raises() -> None:
    with pytest.raises(ExtractionError, match="Could not read PDF"):
        extract_sections(b"this is not a pdf", "application/pdf")


def test_unknown_content_type_raises() -> None:
    with pytest.raises(ExtractionError, match="No extractor"):
        extract_sections(b"data", "image/png")
