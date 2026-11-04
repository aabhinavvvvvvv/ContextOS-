import re

import pytest

from app.ingestion.chunking import Chunk, chunk_sections
from app.ingestion.extract import Section


def long_text(paragraphs: int, sentences_per_paragraph: int) -> str:
    return "\n\n".join(
        " ".join(
            f"Paragraph {p} sentence {s} explains the API decision."
            for s in range(sentences_per_paragraph)
        )
        for p in range(paragraphs)
    )


def words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text))


def test_short_section_becomes_one_unchanged_chunk() -> None:
    sections = [Section(text="We chose REST.", heading="Architecture > API")]

    assert chunk_sections(sections) == [
        Chunk(index=0, text="We chose REST.", heading="Architecture > API", page_number=None)
    ]


def test_long_text_respects_limit_overlaps_and_loses_nothing() -> None:
    text = long_text(paragraphs=8, sentences_per_paragraph=6)

    chunks = chunk_sections([Section(text=text)], max_chars=500, overlap_chars=100)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 500 for chunk in chunks)
    assert words(text) <= words(" ".join(chunk.text for chunk in chunks))  # nothing dropped
    for previous, current in zip(chunks, chunks[1:]):
        overlap = current.text.split("\n\n", 1)[0]
        assert overlap in previous.text[-100:]  # each chunk starts with the previous one's end


def test_chunks_never_cross_sections_and_indexes_continue() -> None:
    sections = [
        Section(text=long_text(3, 6), page_number=1),
        Section(text="Short page two.", page_number=2),
    ]

    chunks = chunk_sections(sections, max_chars=400, overlap_chars=50)

    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert chunks[-1] == Chunk(
        index=len(chunks) - 1, text="Short page two.", heading=None, page_number=2
    )
    assert all(chunk.page_number == 1 for chunk in chunks[:-1])


def test_unbreakable_text_is_hard_split_within_limit() -> None:
    text = "x" * 1050  # no spaces, sentences or paragraphs to split at

    chunks = chunk_sections([Section(text=text)], max_chars=400, overlap_chars=0)

    assert [len(chunk.text) for chunk in chunks] == [400, 400, 250]


@pytest.mark.parametrize("overlap", [-1, 500, 600])
def test_invalid_overlap_is_rejected(overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_sections([Section(text="x")], max_chars=500, overlap_chars=overlap)
