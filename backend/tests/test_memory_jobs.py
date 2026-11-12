"""Memory extraction wiring: /ask queues it, the sweep finds missed turns, GET /memories shows results."""

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import create_db_engine
from app.jobs import get_job_queue
from app.main import app
from app.memory.service import remember_turn, unextracted_turn_ids
from tests.helpers import FakeEmbedder, FakeLLM

pytestmark = pytest.mark.integration


def ask(client: TestClient, question: str) -> dict:
    response = client.post("/ask", json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()


def user_message_id(client: TestClient, conversation_id: str) -> str:
    return client.get(f"/conversations/{conversation_id}").json()["messages"][0]["id"]


def remember(message_id: str, *memories: tuple[str, str]) -> None:
    reply = json.dumps({"memories": [{"kind": k, "content": c} for k, c in memories]})

    async def run() -> None:
        engine = create_db_engine(get_settings())
        try:
            await remember_turn(engine, FakeLLM(reply), FakeEmbedder(), uuid.UUID(message_id))
        finally:
            await engine.dispose()

    asyncio.run(run())


def sweep(limit: int = 10) -> list[str]:
    async def run() -> list[str]:
        engine = create_db_engine(get_settings())
        try:
            async with AsyncSession(engine) as session:
                return [str(i) for i in await unextracted_turn_ids(session, limit=limit)]
        finally:
            await engine.dispose()

    return asyncio.run(run())


# --- /ask queues extraction --------------------------------------------------------


def test_ask_queues_memory_extraction_for_the_users_message(client: TestClient) -> None:
    body = ask(client, "We picked REST. Anything about it?")

    queue = app.dependency_overrides[get_job_queue]()
    assert [str(i) for i in queue.memory_message_ids] == [user_message_id(client, body["conversation_id"])]


class BrokenMemoryQueue:
    async def enqueue_memory_extraction(self, message_id: uuid.UUID) -> bool:
        raise ConnectionError("Redis is down")


def test_ask_still_answers_when_the_queue_is_down(client: TestClient) -> None:
    app.dependency_overrides[get_job_queue] = lambda: BrokenMemoryQueue()

    body = ask(client, "We picked REST.")

    # The turn is saved; the sweep will queue its extraction once Redis is back.
    assert len(client.get(f"/conversations/{body['conversation_id']}").json()["messages"]) == 2


# --- the reconciliation sweep ------------------------------------------------------


def test_sweep_finds_unextracted_user_messages_oldest_first(client: TestClient) -> None:
    first = user_message_id(client, ask(client, "first")["conversation_id"])
    second = user_message_id(client, ask(client, "second")["conversation_id"])
    third = user_message_id(client, ask(client, "third")["conversation_id"])
    remember(second)  # extracted (nothing found): no longer pending

    assert sweep() == [first, third]  # assistant messages never appear
    assert sweep(limit=1) == [first]  # batches, so a backlog can't flood the LLM quota


# --- GET /memories -----------------------------------------------------------------


def test_memories_are_listed_newest_first_with_provenance(client: TestClient) -> None:
    assert client.get("/memories").json() == []
    older = ask(client, "We use REST.")["conversation_id"]
    newer = ask(client, "Short answers please.")["conversation_id"]
    remember(user_message_id(client, older), ("decision", "The team uses REST."))
    remember(user_message_id(client, newer), ("preference", "The user prefers short answers."))

    memories = client.get("/memories").json()

    assert [(m["kind"], m["content"]) for m in memories] == [
        ("preference", "The user prefers short answers."),
        ("decision", "The team uses REST."),
    ]
    # Provenance: you can always open the conversation a memory came from.
    assert memories[1]["conversation_id"] == older
    assert memories[1]["source_message_id"] == user_message_id(client, older)


def remember_with(message_id: str, llm: FakeLLM) -> None:
    async def run() -> None:
        engine = create_db_engine(get_settings())
        try:
            await remember_turn(engine, llm, FakeEmbedder(), uuid.UUID(message_id))
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_changed_decision_supersedes_the_old_memory(client: TestClient) -> None:
    first = user_message_id(client, ask(client, "We use Gemini.")["conversation_id"])
    remember(first, ("decision", "The team uses Gemini."))

    second = user_message_id(client, ask(client, "We switched to Claude.")["conversation_id"])
    llm = FakeLLM(
        '{"memories": [{"kind": "decision", "content": "The team uses Claude.", "replaces": [1]}]}'
    )
    remember_with(second, llm)

    # The model was shown the existing memory, numbered, so it could say what it replaces.
    assert "[1] The team uses Gemini." in llm.calls[0][1]
    # By default only current memories are listed...
    assert [m["content"] for m in client.get("/memories").json()] == ["The team uses Claude."]
    # ...but the old one is kept, pointing at its replacement.
    new, old = client.get("/memories", params={"include_superseded": True}).json()
    assert old["content"] == "The team uses Gemini."
    assert old["superseded_by_id"] == new["id"]
    assert new["superseded_by_id"] is None

    # A superseded memory is never shown to the model again.
    third = user_message_id(client, ask(client, "Any news?")["conversation_id"])
    later_llm = FakeLLM('{"memories": []}')
    remember_with(third, later_llm)
    assert "The team uses Gemini." not in later_llm.calls[0][1]
    assert "[1] The team uses Claude." in later_llm.calls[0][1]


def test_memories_can_be_filtered_by_kind(client: TestClient) -> None:
    conversation_id = ask(client, "We use REST, and keep answers short.")["conversation_id"]
    remember(
        user_message_id(client, conversation_id),
        ("decision", "The team uses REST."),
        ("preference", "The user prefers short answers."),
    )

    decisions = client.get("/memories", params={"kind": "decision"}).json()

    assert [m["content"] for m in decisions] == ["The team uses REST."]
    assert client.get("/memories", params={"kind": "opinion"}).status_code == 422
