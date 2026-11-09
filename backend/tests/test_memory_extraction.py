"""Long-term memory extraction: one conversation turn in, validated memories with provenance out."""

import asyncio
import json
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.config import get_settings
from app.db import create_db_engine
from app.llm import LLMRateLimitError
from app.memory.extraction import (
    EXTRACTION_SYSTEM_PROMPT,
    MAX_MEMORIES_PER_TURN,
    MAX_MEMORY_CHARS,
    ExtractionResult,
    build_extraction_prompt,
    extract_memories,
    parse_memories,
)
from app.memory.service import remember_turn
from app.models import Memory, Message
from tests.helpers import FakeEmbedder, FakeLLM

TURN_DATE = date(2026, 11, 10)  # a Tuesday


def reply(*memories: tuple[str, str]) -> str:
    """What the model returns in JSON mode."""
    return json.dumps({"memories": [{"kind": kind, "content": content} for kind, content in memories]})


# --- pure functions ---------------------------------------------------------------


def test_extraction_prompt_dates_the_turn_and_strips_old_citations() -> None:
    prompt = build_extraction_prompt("We picked REST.", "Noted [1].", TURN_DATE)

    # The date lets the model turn "by Friday" into a date that stays true later.
    assert prompt == (
        "Date of this turn: 2026-11-10 (Tuesday)\n\n"
        "<user_message>\nWe picked REST.\n</user_message>\n\n"
        "<assistant_answer>\nNoted.\n</assistant_answer>"
    )


def test_parse_memories_cleans_deduplicates_and_caps() -> None:
    raw = reply(
        ("preference", "  The user prefers\n short answers. "),
        ("preference", "the user prefers short answers."),  # same memory, different case
        ("fact", "   "),  # empty
        ("fact", "x" * (MAX_MEMORY_CHARS + 1)),  # too long to be one memory
        *[("task", f"Task {i}.") for i in range(10)],
    )

    memories = parse_memories(raw)

    assert [(m.kind, m.content) for m in memories] == [
        ("preference", "The user prefers short answers."),
        *[("task", f"Task {i}.") for i in range(MAX_MEMORIES_PER_TURN - 1)],
    ]


@pytest.mark.parametrize(
    "bad_output",
    ["not json", reply(("opinion", "Cats are great.")), '{"memories": "none"}', "{}"],
)
def test_unusable_model_output_means_no_memories(bad_output: str) -> None:
    memories = asyncio.run(extract_memories(FakeLLM(bad_output), "We picked REST.", "Noted.", TURN_DATE))

    assert memories == []


def test_extraction_asks_for_json_in_the_expected_shape() -> None:
    llm = FakeLLM(reply(("decision", "The team chose REST over GraphQL.")))

    memories = asyncio.run(extract_memories(llm, "We picked REST.", "Noted.", TURN_DATE))

    assert [(m.kind, m.content) for m in memories] == [("decision", "The team chose REST over GraphQL.")]
    [(system, _)] = llm.calls
    assert system == EXTRACTION_SYSTEM_PROMPT
    assert llm.json_schemas == [ExtractionResult.model_json_schema()]


# --- remember_turn: database + fake LLM ----------------------------------------------


def make_turn(client: TestClient, question: str = "We switched to Gemini. Anything on setup?") -> tuple[str, str]:
    """Record a real turn through /ask; returns (user message id, assistant message id)."""
    body = client.post("/ask", json={"question": question}).json()
    user, assistant = client.get(f"/conversations/{body['conversation_id']}").json()["messages"]
    return user["id"], assistant["id"]


def remember(message_id: str, llm: FakeLLM) -> tuple[int, list[Memory], Message]:
    """Run remember_turn, then read back every memory and the source message."""

    async def run() -> tuple[int, list[Memory], Message]:
        engine = create_db_engine(get_settings())
        try:
            stored = await remember_turn(engine, llm, FakeEmbedder(), uuid.UUID(message_id))
            async with AsyncSession(engine) as session:
                memories = (await session.scalars(select(Memory).options(undefer(Memory.embedding)))).all()
                message = await session.get(Message, uuid.UUID(message_id))
            return stored, list(memories), message
        finally:
            await engine.dispose()

    return asyncio.run(run())



@pytest.mark.integration
def test_memories_are_stored_with_embedding_and_provenance(client: TestClient) -> None:
    user_id, _ = make_turn(client)
    content = "The user switched the LLM provider to Gemini."
    llm = FakeLLM(reply(("decision", content)))

    stored, [memory], message = remember(user_id, llm)

    assert stored == 1
    assert (memory.kind, memory.content) == ("decision", content)
    assert str(memory.source_message_id) == user_id  # provenance: which turn it came from
    assert list(memory.embedding) == pytest.approx(FakeEmbedder.vector(content), abs=1e-6)
    assert message.memories_extracted_at is not None
    assert "<user_message>\nWe switched to Gemini. Anything on setup?\n</user_message>" in llm.calls[0][1]


@pytest.mark.integration
def test_remembering_a_turn_twice_changes_nothing_and_costs_no_llm_call(client: TestClient) -> None:
    user_id, _ = make_turn(client)
    remember(user_id, FakeLLM(reply(("decision", "The user switched to Gemini."))))
    second_llm = FakeLLM(reply(("decision", "The user switched to Gemini.")))

    stored, memories, _ = remember(user_id, second_llm)

    # Job queues may run a job twice (retries, the reconciliation sweep): it must be harmless.
    assert stored == 0
    assert len(memories) == 1
    assert second_llm.calls == []


@pytest.mark.integration
def test_a_turn_with_nothing_to_remember_is_still_marked_done(client: TestClient) -> None:
    user_id, _ = make_turn(client, "Which database do we use?")

    stored, memories, message = remember(user_id, FakeLLM(reply()))

    assert (stored, memories) == (0, [])
    assert message.memories_extracted_at is not None  # so it isn't retried forever


@pytest.mark.integration
def test_llm_failure_stores_nothing_and_leaves_the_turn_for_a_retry(client: TestClient) -> None:
    user_id, _ = make_turn(client)

    with pytest.raises(LLMRateLimitError):
        remember(user_id, FakeLLM(error=LLMRateLimitError("quota")))

    retry_llm = FakeLLM(reply(("decision", "The user switched to Gemini.")))
    stored, memories, message = remember(user_id, retry_llm)  # the retry succeeds

    assert len(retry_llm.calls) == 1  # the failed run didn't mark the turn as done
    assert stored == len(memories) == 1  # and left nothing half-written
    assert message.memories_extracted_at is not None


class SlowFakeLLM(FakeLLM):
    """Takes a moment to reply, so two overlapping jobs are both waiting on it at once."""

    async def generate(self, **kwargs):  # type: ignore[override]
        await asyncio.sleep(0.2)
        return await super().generate(**kwargs)


@pytest.mark.integration
def test_overlapping_runs_store_the_memories_once(client: TestClient) -> None:
    user_id = uuid.UUID(make_turn(client)[0])
    llm = SlowFakeLLM(reply(("decision", "The user switched to Gemini.")))

    async def two_runs_at_once() -> tuple[list[int], int]:
        engine = create_db_engine(get_settings())
        try:
            stored = await asyncio.gather(
                remember_turn(engine, llm, FakeEmbedder(), user_id),
                remember_turn(engine, llm, FakeEmbedder(), user_id),
            )
            async with AsyncSession(engine) as session:
                count = await session.scalar(select(func.count()).select_from(Memory))
            return sorted(stored), count
        finally:
            await engine.dispose()

    stored, count = asyncio.run(two_runs_at_once())

    assert len(llm.calls) == 2  # both got past the early check (that's the overlap)...
    assert (stored, count) == ([0, 1], 1)  # ...but only one saved its memories


@pytest.mark.integration
def test_only_user_messages_are_extracted(client: TestClient) -> None:
    _, assistant_id = make_turn(client)
    llm = FakeLLM()

    stored, _, _ = remember(assistant_id, llm)

    assert stored == 0
    assert llm.calls == []
