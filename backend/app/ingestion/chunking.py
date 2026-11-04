"""Chunking: split sections into pieces small enough to embed and retrieve precisely.

Strategy: structure-aware (a chunk never crosses a section), recursive (split at paragraphs,
then sentences, then words, only as far as needed) and overlapping (each chunk repeats the
end of the previous one, so a sentence cut at a boundary still appears whole somewhere).

Sizes are in characters (~4 characters per English token) until we have a tokenizer in Phase 2.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from app.ingestion.extract import Section

DEFAULT_MAX_CHARS = 1500
DEFAULT_OVERLAP_CHARS = 200

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    index: int  # position within the document, starting at 0
    text: str
    heading: str | None
    page_number: int | None


def chunk_sections(
    sections: list[Section],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    if not 0 <= overlap_chars < max_chars:
        raise ValueError("overlap_chars must be >= 0 and smaller than max_chars")

    chunks: list[Chunk] = []
    for section in sections:
        for text in _split_text(section.text, max_chars, overlap_chars):
            chunks.append(
                Chunk(
                    index=len(chunks),
                    text=text,
                    heading=section.heading,
                    page_number=section.page_number,
                )
            )
    return chunks


def _split_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Greedily pack paragraphs into chunks of at most max_chars, with overlap between them."""
    chunks: list[str] = []
    current = ""
    for piece in _pieces(text, max_chars):
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
            continue
        chunks.append(current)
        overlap = _tail(current, overlap_chars)
        with_overlap = f"{overlap}\n\n{piece}"
        current = with_overlap if overlap and len(with_overlap) <= max_chars else piece
    if current:
        chunks.append(current)
    return chunks


def _pieces(text: str, max_chars: int) -> Iterator[str]:
    """Yield paragraphs, breaking any paragraph longer than max_chars into smaller pieces."""
    for paragraph in _PARAGRAPH_BREAK.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            yield paragraph
        else:
            yield from _split_long_paragraph(paragraph, max_chars)


def _split_long_paragraph(paragraph: str, max_chars: int) -> Iterator[str]:
    """Pack sentences into pieces of at most max_chars; hard-split any sentence that is too long."""
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > max_chars:
            if current:
                yield current
                current = ""
            # Cut at the last space before the limit so we don't split a word, if possible.
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            yield sentence[:cut]
            sentence = sentence[cut:].lstrip()
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            yield current
            current = sentence
    if current:
        yield current


def _tail(text: str, overlap_chars: int) -> str:
    """The last ~overlap_chars of text, starting at a word boundary."""
    if overlap_chars == 0:
        return ""
    tail = text[-overlap_chars:]
    first_space = tail.find(" ")
    return tail[first_space + 1 :] if first_space != -1 else tail
