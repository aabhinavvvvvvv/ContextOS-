"""Managing memories: expiry, correcting and deleting."""

import asyncio
import json
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import create_db_engine
from app.memory.extraction import parse_memories
from app.models import Memory
from tests.helpers import FakeEmbedder, FakeLLM
from tests.test_answers import use_llm
from tests.test_memory_answers import store_memory
from tests.test_memory_jobs import ask, remember_with, user_message_id


def test_parse_memories_keeps_the_expiry_date() -> None:
    raw = json.dumps(
        {
            "memories": [
                {"kind": "fact", "content": "The user is on vacation this week.", "expires_on": "2026-11-15"},
                {"kind": "task", "content": "The user needs to finish the tests by 2026-11-13."},
            ]
        }
    )

    vacation, task = parse_memories(raw)

    assert vacation.expires_on == date(2026, 11, 15)
    assert task.expires_on is None  # a deadline is not an expiry


def store_with(client: TestClient, memory: dict) -> None:
    """Record a turn, then extract exactly this memory (any fields) from it."""
    body = ask(client, "Please remember this.")
    remember_with(
        user_message_id(client, body["conversation_id"]), FakeLLM(json.dumps({"memories": [memory]}))
    )


def contents(memories: list[dict]) -> list[str]:
    return [m["content"] for m in memories]


# --- expiry ------------------------------------------------------------------------------


@pytest.mark.integration
def test_expired_memories_are_not_current_and_not_recalled(client: TestClient) -> None:
    # Dates far from today, so the test can't flip at midnight or in another time zone.
    store_with(client, {"kind": "fact", "content": "The user is on vacation.", "expires_on": "2020-01-31"})
    store_with(client, {"kind": "fact", "content": "The user leads the platform team.", "expires_on": "2999-12-31"})

    assert contents(client.get("/memories").json()) == ["The user leads the platform team."]
    history = client.get("/memories", params={"include_history": True}).json()
    assert sorted(m["expires_on"] for m in history) == ["2020-01-31", "2999-12-31"]

    llm = FakeLLM("Leading the platform team [M1].")
    use_llm(llm)
    client.post("/ask", json={"question": "What is the user doing?"})
    assert "vacation" not in llm.calls[-1][1]


# --- correcting ---------------------------------------------------------------------------


def stored_embedding(memory_id: str) -> list[float]:
    async def run() -> list[float]:
        engine = create_db_engine(get_settings())
        try:
            async with engine.connect() as connection:
                vector = await connection.scalar(
                    select(Memory.embedding).where(Memory.id == uuid.UUID(memory_id))
                )
                return [float(x) for x in vector]
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.mark.integration
def test_a_memory_can_be_corrected_and_is_re_embedded(client: TestClient) -> None:
    store_memory(client, "The team uses Gemni.")
    memory_id = client.get("/memories").json()[0]["id"]

    response = client.patch(f"/memories/{memory_id}", json={"content": "  The team uses   Gemini. "})

    assert response.status_code == 200
    assert response.json()["content"] == "The team uses Gemini."
    assert contents(client.get("/memories").json()) == ["The team uses Gemini."]
    assert stored_embedding(memory_id) == pytest.approx(FakeEmbedder.vector("The team uses Gemini."))


@pytest.mark.integration
def test_correcting_rejects_blank_text_and_unknown_memories(client: TestClient) -> None:
    store_memory(client, "The team uses Gemini.")
    memory_id = client.get("/memories").json()[0]["id"]

    assert client.patch(f"/memories/{memory_id}", json={"content": "   "}).status_code == 422
    assert client.patch(f"/memories/{uuid.uuid4()}", json={"content": "x"}).status_code == 404


# --- deleting -----------------------------------------------------------------------------


@pytest.mark.integration
def test_deleting_a_wrong_update_brings_back_the_memory_it_replaced(client: TestClient) -> None:
    store_memory(client, "The team uses Gemini as the LLM.")
    store_with(client, {"kind": "decision", "content": "The team switched the LLM to Claude.", "replaces": [1]})
    [update] = client.get("/memories").json()
    assert update["content"] == "The team switched the LLM to Claude."

    assert client.delete(f"/memories/{update['id']}").status_code == 204

    [restored] = client.get("/memories", params={"include_history": True}).json()
    assert (restored["content"], restored["superseded_by_id"], restored["superseded_at"]) == (
        "The team uses Gemini as the LLM.",
        None,
        None,
    )
    assert client.delete(f"/memories/{update['id']}").status_code == 404
