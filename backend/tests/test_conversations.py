"""Conversation storage: every /ask turn is recorded, so memory can later be extracted from it."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.conversations.service import make_title, record_turn
from app.db import create_db_engine
from app.llm import LLMError, get_llm
from app.main import app
from tests.helpers import FakeLLM

pytestmark = pytest.mark.integration

NOTES = b"# Architecture\nWe use FastAPI.\n\n## API\nREST, not GraphQL.\n"


def upload(client: TestClient) -> None:
    assert client.post("/documents", files={"file": ("notes.md", NOTES)}).status_code == 201


def ask(client: TestClient, question: str, conversation_id: str | None = None) -> dict:
    payload: dict = {"question": question}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    response = client.post("/ask", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_first_question_starts_a_conversation_with_both_messages(client: TestClient) -> None:
    upload(client)
    app.dependency_overrides[get_llm] = lambda: FakeLLM("We use REST [1].")

    body = ask(client, "Which API style did we pick?")

    conversation = client.get(f"/conversations/{body['conversation_id']}").json()
    assert conversation["title"] == "Which API style did we pick?"
    user, assistant = conversation["messages"]
    assert (user["position"], user["role"], user["content"]) == (0, "user", "Which API style did we pick?")
    assert user["citations"] == []
    assert (assistant["position"], assistant["role"], assistant["content"]) == (
        1,
        "assistant",
        "We use REST [1].",
    )
    # The citation is a snapshot of what the user saw, so it survives re-ingestion of the document.
    assert assistant["citations"] == body["citations"]
    assert assistant["citations"][0]["filename"] == "notes.md"


def test_follow_up_appends_to_the_same_conversation(client: TestClient) -> None:
    upload(client)
    first = ask(client, "Which API style?")

    second = ask(client, "And which framework?", conversation_id=first["conversation_id"])

    assert second["conversation_id"] == first["conversation_id"]
    conversation = client.get(f"/conversations/{first['conversation_id']}").json()
    assert conversation["title"] == "Which API style?"  # the first question names the thread
    assert [(m["position"], m["role"]) for m in conversation["messages"]] == [
        (0, "user"),
        (1, "assistant"),
        (2, "user"),
        (3, "assistant"),
    ]
    assert conversation["messages"][2]["content"] == "And which framework?"
    assert len(client.get("/conversations").json()) == 1


def test_not_found_answers_are_recorded_too(client: TestClient) -> None:
    body = ask(client, "Anything at all?")  # no documents: answered without calling the LLM

    messages = client.get(f"/conversations/{body['conversation_id']}").json()["messages"]
    assert [m["content"] for m in messages] == ["Anything at all?", body["answer"]]


def test_unknown_conversation_is_404_before_spending_llm_quota(client: TestClient) -> None:
    upload(client)
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm

    response = client.post("/ask", json={"question": "REST?", "conversation_id": str(uuid.uuid4())})

    assert response.status_code == 404
    assert llm.calls == []


def test_failed_answer_stores_nothing(client: TestClient) -> None:
    upload(client)
    app.dependency_overrides[get_llm] = lambda: FakeLLM(error=LLMError("upstream failed"))

    assert client.post("/ask", json={"question": "REST?"}).status_code == 502

    # A turn is a question AND its answer: half a turn would mislead memory extraction later.
    assert client.get("/conversations").json() == []


def test_conversations_are_listed_most_recently_active_first(client: TestClient) -> None:
    upload(client)
    older = ask(client, "First thread")["conversation_id"]
    newer = ask(client, "Second thread")["conversation_id"]
    assert [c["id"] for c in client.get("/conversations").json()] == [newer, older]

    ask(client, "Back to the first", conversation_id=older)

    assert [c["id"] for c in client.get("/conversations").json()] == [older, newer]


def test_unknown_conversation_id_on_read_is_404(client: TestClient) -> None:
    assert client.get(f"/conversations/{uuid.uuid4()}").status_code == 404


def test_simultaneous_turns_on_one_conversation_get_distinct_positions(client: TestClient) -> None:
    conversation_id = uuid.UUID(ask(client, "Start")["conversation_id"])

    async def two_turns_at_once() -> None:
        engine = create_db_engine(get_settings())
        try:

            async def turn(n: int) -> None:
                # Separate sessions = separate database connections, like two API requests.
                async with AsyncSession(engine) as session:
                    await record_turn(
                        session,
                        conversation_id=conversation_id,
                        question=f"q{n}",
                        answer=f"a{n}",
                        citations=[],
                    )

            await asyncio.gather(turn(1), turn(2))
        finally:
            await engine.dispose()

    asyncio.run(two_turns_at_once())

    messages = client.get(f"/conversations/{conversation_id}").json()["messages"]
    assert [m["position"] for m in messages] == [0, 1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    ("question", "title"),
    [
        ("Short question?", "Short question?"),
        ("Line one\n\nline   two", "Line one line two"),
        ("word " * 30, ("word " * 16).strip() + "…"),  # cut at a word boundary, under 80 chars
    ],
)
def test_make_title(question: str, title: str) -> None:
    assert make_title(question) == title
