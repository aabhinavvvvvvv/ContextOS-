"""Calls the real Gemini API. Spends free-tier quota, so it only runs on request:

    uv run pytest -m llm
"""

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest

from app.answers.planning import plan_steps
from app.answers.service import (
    NOT_FOUND_ANSWER,
    SYSTEM_PROMPT,
    build_prompt,
    question_with_parts,
    standalone_question,
)
from app.llm import LLM, LLMNotConfiguredError, get_llm
from app.memory.extraction import extract_memories
from app.memory.recall import RecalledMemory
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


def test_gemini_answers_from_memories_and_prefers_them_over_an_outdated_document() -> None:
    sources = [source("ContextOS uses Gemini as its LLM because the free tier is enough.", "Stack > LLM")]
    said_on = datetime(2026, 11, 12, tzinfo=UTC)
    memories = [
        RecalledMemory(uuid.uuid4(), "decision", "The team switched the ContextOS LLM from Gemini to Claude on 2026-11-12.", said_on),
        RecalledMemory(uuid.uuid4(), "preference", "The user prefers a dark theme in their editor.", said_on),
    ]
    llm = fresh_llm()

    async def run() -> list[str]:
        return [
            (await llm.generate(system=SYSTEM_PROMPT, prompt=build_prompt(q, sources, memories))).text
            for q in [
                "Which LLM does ContextOS use?",  # memory vs. outdated document
                "Which editor theme does the user like?",  # only a memory knows
                "What is our database backup schedule?",  # nobody knows
            ]
        ]

    conflict, memory_only, unknown = asyncio.run(run())
    print(f"\nconflict:    {conflict!r}\nmemory only: {memory_only!r}\nunknown:     {unknown!r}")

    assert "claude" in conflict.lower() and "[M1]" in conflict
    assert "[M2]" not in conflict  # the unrelated memory is ignored
    assert "dark" in memory_only.lower() and "[M2]" in memory_only
    assert NOT_FOUND_ANSWER in unknown


# Planning eval: (question, allowed numbers of steps, words that must each appear in some
# step question, tools that must be used). Lowercase substring checks.
PLAN_CASES = [
    # The project's example: a search and the task list; "prepare a plan" is done when answering.
    (
        "What did we decide about the API architecture, what tasks are unfinished, and prepare a prioritized plan for today.",
        (2,),
        ["api", "task"],
        {"search", "list_tasks"},
    ),
    ("Which LLM do we use and why?", (1,), ["llm"], {"search"}),  # one question
    ("Who owns the backend, and when is the demo?", (2,), ["backend", "demo"], {"search"}),
    ("What is pgvector, and how does ContextOS use hybrid search?", (2,), ["pgvector", "hybrid"], {"search"}),
    ("What should I work on today?", (1, 2), [], {"list_tasks"}),
]


def test_gemini_plans_steps_and_picks_tools() -> None:
    llm = fresh_llm()

    async def run() -> list:
        return [(await plan_steps(llm, question))[0] for question, *_ in PLAN_CASES]

    failures = []
    for (question, counts, words, tools), steps in zip(PLAN_CASES, asyncio.run(run()), strict=True):
        parts = [s.question.lower() for s in steps]
        ok = (
            len(steps) in counts
            and all(any(w in p for p in parts) for w in words)
            and tools <= {s.tool for s in steps}
        )
        print(f"\n{'ok  ' if ok else 'FAIL'} {question}")
        for s in steps:
            print(f"       - {s.tool}: {s.question}")
        if not ok:
            failures.append(question[:40])

    assert failures == [], f"{len(failures)}/{len(PLAN_CASES)} cases failed: {failures}"


def test_gemini_answers_the_parts_it_can_and_names_the_missing_part() -> None:
    sources = [source("We chose REST over GraphQL because caching was simpler.", "Decisions > API")]
    question = question_with_parts(
        "Which API style did we choose, and what is our database backup schedule?",
        ["Which API style did we choose?", "What is our database backup schedule?"],
    )

    [answer] = ask_all([question], sources)
    print(f"\npartial: {answer!r}")

    assert "REST" in answer and "[1]" in answer  # the part it can answer
    # Not a whole-answer refusal, and the missing part is named (the model may reuse the
    # "couldn't find" sentence for that part: seen live).
    assert answer.strip() != NOT_FOUND_ANSWER and "backup" in answer.lower()


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


# Memory extraction eval. Each case: (user message, assistant answer,
#   expected memory count or None for "any",
#   required: [(kind, [[any of these words], [and any of these], ...])], each matched by one memory,
#   forbidden: words no memory may contain). Lowercase substring checks.
MEMORY_CASES = [
    # Nothing to remember: a question; document content in the answer; small talk.
    ("Which database does ContextOS use?", "ContextOS uses PostgreSQL with pgvector [1].", 0, [], []),
    ("What did we decide about the API?", "You decided on REST, not GraphQL [1].", 0, [], []),
    ("Thanks, that's helpful!", "You're welcome.", 0, [], []),
    # A decision with its reason; the README fact in the answer must not be extracted.
    (
        "We decided to switch the LLM provider to Gemini because it has a free tier. "
        "What does the README say about configuring it?",
        "The README says to set GEMINI_API_KEY in .env [1].",
        None,
        [("decision", [["gemini"], ["free"]])],
        ["gemini_api_key", ".env"],
    ),
    (
        "Please keep your answers short, I read them on my phone.",
        "I couldn't find this in your documents.",
        None,
        [("preference", [["short", "concise", "brief"]])],
        [],
    ),
    # A task with a relative date that must become absolute (the turn is on Tuesday 2026-11-10).
    (
        "I need to finish the upload tests by Friday. Which tests exist already?",
        "There are tests for uploads and duplicate files [1].",
        None,
        [("task", [["upload"], ["2026-11-13", "november 13", "13 november"]])],
        ["duplicate"],
    ),
    (
        "Our team is three engineers, and I'm the one responsible for the backend.",
        "I couldn't find this in your documents.",
        2,  # one fact per memory: team size and role are separate facts
        [("fact", [["three", "3"]]), ("fact", [["backend"]])],
        [],
    ),
    # Secrets are never stored, even when the user asks.
    ("Remember that the staging database password is hunter2.", "I couldn't find this in your documents.", None, [], ["hunter2"]),
]


def test_gemini_extracts_only_what_is_worth_remembering() -> None:
    llm = fresh_llm()
    turn_date = date(2026, 11, 10)

    async def run() -> list:
        return [
            await extract_memories(llm, user, answer, turn_date)
            for user, answer, _, _, _ in MEMORY_CASES
        ]

    failures = []
    for (user, _, count, required, forbidden), memories in zip(MEMORY_CASES, asyncio.run(run()), strict=True):
        found = [(m.kind, m.content.lower()) for m in memories]
        problems = []
        if count is not None and len(found) != count:
            problems.append(f"expected {count} memories")
        for kind, groups in required:
            if not any(k == kind and all(any(w in text for w in group) for group in groups) for k, text in found):
                problems.append(f"no {kind} matching {groups}")
        problems += [f"contains {w!r}" for w in forbidden if any(w in text for _, text in found)]
        print(f"\n{'FAIL' if problems else 'ok  '} {user[:60]!r}")
        for m in memories:
            print(f"       {m.kind:<10} {m.content}")
        if problems:
            print(f"       problems: {problems}")
            failures.append(user[:40])

    assert failures == [], f"{len(failures)}/{len(MEMORY_CASES)} cases failed: {failures}"


# Dedup / update eval: (existing memories, user message, expected outcome).
#   "duplicate": nothing new; "update": one memory that replaces [1]; "add": new memory, replaces nothing.
GEMINI = "The team decided to keep Gemini as their LLM because the free tier is enough."
UPDATE_CASES = [
    ([GEMINI], "Just to confirm, we are staying with Gemini for now. Any notes on its setup?", "duplicate"),
    ([GEMINI], "We switched the LLM provider from Gemini to Claude last week.", "update"),
    (["The user prefers short answers."], "Actually, I want detailed answers with examples from now on.", "update"),
    (["The user needs to finish the upload tests by 2026-11-13."], "I finished the upload tests today. What should I test next?", "update"),
    (["The user prefers short answers."], "We decided to use SAQ for background jobs.", "add"),
]


def test_gemini_skips_known_memories_and_marks_replacements() -> None:
    llm = fresh_llm()

    async def run() -> list:
        return [
            await extract_memories(llm, user, "Noted.", date(2026, 11, 12), existing)
            for existing, user, _ in UPDATE_CASES
        ]

    failures = []
    for (_, user, expected), memories in zip(UPDATE_CASES, asyncio.run(run()), strict=True):
        replaced = [m.replaces for m in memories]
        ok = {
            "duplicate": memories == [],
            "update": len(memories) == 1 and replaced == [[1]],
            "add": len(memories) >= 1 and all(r == [] for r in replaced),
        }[expected]
        print(f"\n{'ok  ' if ok else 'FAIL'} [{expected}] {user[:60]!r}")
        for m in memories:
            print(f"       {m.kind:<10} replaces={m.replaces} {m.content}")
        if not ok:
            failures.append(user[:40])

    assert failures == [], f"{len(failures)}/{len(UPDATE_CASES)} cases failed: {failures}"


def test_gemini_sets_an_expiry_only_for_time_bound_memories() -> None:
    llm = fresh_llm()
    tuesday = date(2026, 11, 10)

    async def run() -> list:
        return [
            await extract_memories(llm, user, "Noted.", tuesday)
            for user in [
                "I'm on vacation until Friday, so I won't review any PRs.",
                "I need to finish the upload tests by Friday.",
            ]
        ]

    vacation, deadline = asyncio.run(run())
    print(f"\nvacation: {vacation}\ndeadline: {deadline}")

    # One fact per memory: "on vacation" and "won't review PRs" may be separate; all end Friday.
    assert vacation and all(m.expires_on == date(2026, 11, 13) for m in vacation)
    assert any("vacation" in m.content.lower() for m in vacation)
    assert [(m.kind, m.expires_on) for m in deadline] == [("task", None)]
