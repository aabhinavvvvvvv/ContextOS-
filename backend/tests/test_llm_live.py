"""Calls the real Gemini API. Spends free-tier quota, so it only runs on request:

    uv run pytest -m llm
"""

import asyncio
import uuid

import pytest

from app.answers.service import (
    NOT_FOUND_ANSWER,
    SYSTEM_PROMPT,
    build_prompt,
    standalone_question,
)
from app.llm import LLM, LLMNotConfiguredError, get_llm
from app.search.service import SearchHit

pytestmark = pytest.mark.llm


def fresh_llm() -> LLM:
    """A new client per test: each test's asyncio.run creates (then closes) its own event
    loop, and a client's open connections belong to the loop they were opened on."""
    get_llm.cache_clear()
    try:
        return get_llm()
    except LLMNotConfiguredError:
        pytest.skip("GEMINI_API_KEY is not set")


def source(text: str, heading: str) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(), document_id=uuid.uuid4(), filename="decisions.md",
        chunk_index=0, heading=heading, page_number=None, text=text, score=0.0,
    )


def ask_all(questions: list[str], sources: list[SearchHit]) -> list[str]:
    llm = fresh_llm()

    # One asyncio.run for all of a test's calls (see fresh_llm).
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


# Follow-up rewriting eval: (history, follow-up, the rewrite must contain one of these,
# and must contain none of those). Lowercase substring checks.
# The assistant's real answer from a live run, whose wording leaked into a bad rewrite
# ("What database and storage technologies does the ContextOS frontend use?").
DB = [
    ("user", "Which database does ContextOS use?"),
    (
        "assistant",
        "ContextOS uses the following databases and storage technologies:\n"
        "- PostgreSQL [5]\n- pgvector [5]\n- Redis [5]",
    ),
]
REWRITE_CASES = [
    # Topic shift: "what about X?" asks the previous question about X, not about both.
    (DB, "What about the frontend?", ["frontend"], ["database", "storage"]),
    (
        [("user", "Which language is the backend written in?"), ("assistant", "Python [1].")],
        "And the mobile app?",
        ["mobile"],
        ["backend"],
    ),
    # Reference resolution: "why?", "it", "tell me more".
    (
        [("user", "What did we move to a worker?"), ("assistant", "Ingestion, to a Redis-backed worker [1].")],
        "Why?",
        ["ingestion"],
        [],
    ),
    (
        [("user", "Which embedding model do we use?"), ("assistant", "BAAI/bge-small-en-v1.5 [2].")],
        "How many dimensions does it have?",
        ["bge-small", "embedding model"],
        [],
    ),
    (
        [("user", "Which API style did we pick?"), ("assistant", "REST, not GraphQL [1].")],
        "Tell me more",
        ["rest"],
        [],
    ),
    # Already standalone, or a new topic: no old subject leaks in.
    (DB, "What is Phase 4 about?", ["phase 4"], ["database", "postgres"]),
    (DB, "Which tasks are still open?", ["task"], ["database", "postgres"]),
]


def test_gemini_rewrites_follow_ups_into_standalone_questions() -> None:
    llm = fresh_llm()

    async def run() -> list[str]:
        return [
            (await standalone_question(llm, follow_up, history)).question
            for history, follow_up, _, _ in REWRITE_CASES
        ]

    failures = []
    for (_, follow_up, must_have_one, must_not_have), rewritten in zip(
        REWRITE_CASES, asyncio.run(run()), strict=True
    ):
        text = rewritten.lower()
        ok = any(w in text for w in must_have_one) and not any(w in text for w in must_not_have)
        print(f"\n{'ok  ' if ok else 'FAIL'} {follow_up!r} -> {rewritten!r}")
        if not ok:
            failures.append(follow_up)

    assert failures == [], f"{len(failures)}/{len(REWRITE_CASES)} rewrites failed: {failures}"
