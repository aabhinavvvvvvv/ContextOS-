"""Calls the real Gemini API. Spends free-tier quota, so it only runs on request:

    uv run pytest -m llm
"""

import asyncio
import uuid

import pytest

from app.answers.service import NOT_FOUND_ANSWER, SYSTEM_PROMPT, build_prompt
from app.llm import LLMNotConfiguredError, get_llm
from app.search.service import SearchHit

pytestmark = pytest.mark.llm


def source(text: str, heading: str) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(), document_id=uuid.uuid4(), filename="decisions.md",
        chunk_index=0, heading=heading, page_number=None, text=text, score=0.0,
    )


def ask_all(questions: list[str], sources: list[SearchHit]) -> list[str]:
    try:
        llm = get_llm()
    except LLMNotConfiguredError:
        pytest.skip("GEMINI_API_KEY is not set")

    # One asyncio.run for all calls: the cached client's connections belong to the event
    # loop they were opened on, and every asyncio.run creates (then closes) a new loop.
    async def run() -> list[str]:
        return [
            (await llm.generate(system=SYSTEM_PROMPT, prompt=build_prompt(q, sources))).text
            for q in questions
        ]

    return asyncio.run(run())


def test_gemini_answers_from_the_source_and_cites_it_or_admits_it_cannot() -> None:
    sources = [source("We chose REST over GraphQL because caching was simpler.", "Decisions > API")]

    answered, unanswerable = ask_all(
        ["Which API style did we choose, and why?", "What is our database backup schedule?"],
        sources,
    )
    print(f"\nanswered:     {answered!r}\nunanswerable: {unanswerable!r}")

    assert "REST" in answered and "[1]" in answered
    assert NOT_FOUND_ANSWER in unanswerable
