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


def ask(question: str, sources: list[SearchHit]) -> str:
    try:
        llm = get_llm()
    except LLMNotConfiguredError:
        pytest.skip("GEMINI_API_KEY is not set")
    result = asyncio.run(llm.generate(system=SYSTEM_PROMPT, prompt=build_prompt(question, sources)))
    return result.text


def test_gemini_answers_from_the_source_and_cites_it_or_admits_it_cannot() -> None:
    sources = [source("We chose REST over GraphQL because caching was simpler.", "Decisions > API")]

    answered = ask("Which API style did we choose, and why?", sources)
    unanswerable = ask("What is our database backup schedule?", sources)

    assert "REST" in answered and "[1]" in answered
    assert NOT_FOUND_ANSWER in unanswerable
