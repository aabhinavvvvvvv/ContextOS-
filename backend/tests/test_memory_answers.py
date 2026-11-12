"""Memories in answers: /ask recalls the current memories closest to the question, and the
model may cite them as [Mn] next to the document sources [n]."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.answers.service import build_prompt, cited_memories, strip_citations
from app.memory.recall import RecalledMemory
from tests.helpers import FakeLLM
from tests.test_answers import hit, upload, use_llm
from tests.test_memory_jobs import ask, remember, remember_with, user_message_id


def memory(content: str, kind: str = "decision") -> RecalledMemory:
    return RecalledMemory(
        id=uuid.uuid4(), kind=kind, content=content, created_at=datetime(2026, 11, 12, 9, 30, tzinfo=UTC)
    )


# --- pure functions -----------------------------------------------------------------


def test_build_prompt_puts_dated_memories_before_the_sources() -> None:
    prompt = build_prompt(
        "Which LLM?",
        [hit("We use Gemini.", heading="Stack")],
        [memory("The team switched the LLM to Claude.")],
    )

    assert prompt == (
        "Memories:\n\n"
        '<memory id="M1" kind="decision" date="2026-11-12">\nThe team switched the LLM to Claude.\n</memory>\n\n'
        "Sources:\n\n"
        '<source id="1" document="doc.md" location="Stack">\nWe use Gemini.\n</source>\n\n'
        "Question: Which LLM?\n\n"
        "Answer using only the sources and memories above, citing them like [1] or [M1]."
    )


def test_build_prompt_with_memories_but_no_sources() -> None:
    prompt = build_prompt("How long should answers be?", [], [memory("The user prefers short answers.")])

    assert "\n\nSources: none\n\n" in prompt


def test_cited_memories_are_verified_deduplicated_and_in_first_cited_order() -> None:
    memories = [memory("a"), memory("b")]

    cited = cited_memories("B [M2]. A [M1][M2], the notes agree [1]. Ignore [M9] and [M0].", memories)

    # [1] is a document citation, [M9]/[M0] match no memory, the second [M2] is a repeat.
    assert [(c.number, c.memory.content) for c in cited] == [(2, "b"), (1, "a")]


def test_strip_citations_removes_memory_markers_too() -> None:
    assert strip_citations("Claude [M1], per the notes [2].") == "Claude, per the notes."


# --- POST /ask ------------------------------------------------------------------------


def store_memory(client: TestClient, content: str, kind: str = "decision") -> None:
    """Record a turn, then "extract" one memory from it with a scripted extraction reply."""
    body = ask(client, "Please remember this.")
    remember(user_message_id(client, body["conversation_id"]), (kind, content))


@pytest.mark.integration
def test_ask_gives_the_model_memories_and_returns_the_cited_ones(client: TestClient) -> None:
    upload(client)
    store_memory(client, "The team switched the LLM to Claude.")
    llm = FakeLLM("We use Claude [M1]; the notes mention FastAPI [1].")
    use_llm(llm)

    body = client.post("/ask", json={"question": "Which LLM do we use?"}).json()

    prompt = llm.calls[-1][1]
    assert '<memory id="M1" kind="decision"' in prompt and "<source id=\"1\"" in prompt
    stored = client.get("/memories").json()[0]
    assert [(m["number"], m["memory_id"], m["content"]) for m in body["memory_citations"]] == [
        (1, stored["id"], "The team switched the LLM to Claude.")
    ]
    # Saved with the answer, as shown at the time.
    messages = client.get(f"/conversations/{body['conversation_id']}").json()["messages"]
    assert messages[1]["memory_citations"] == body["memory_citations"]
    assert messages[0]["memory_citations"] == []


@pytest.mark.integration
def test_a_memory_alone_is_enough_to_answer(client: TestClient) -> None:
    store_memory(client, "The user prefers short answers.", kind="preference")  # no documents
    llm = FakeLLM("Short ones [M1].")
    use_llm(llm)

    body = client.post("/ask", json={"question": "How long should answers be?"}).json()

    assert body["answer"] == "Short ones [M1]."  # the model was called, not "couldn't find"
    assert body["sources_considered"] == 0
    assert len(body["memory_citations"]) == 1


@pytest.mark.integration
def test_superseded_memories_are_not_given_to_the_model(client: TestClient) -> None:
    store_memory(client, "The team uses Gemini as the LLM.")
    body = ask(client, "We switched to Claude.")
    replacement = {"kind": "decision", "content": "The team switched the LLM to Claude.", "replaces": [1]}
    remember_with(
        user_message_id(client, body["conversation_id"]),
        FakeLLM(json.dumps({"memories": [replacement]})),
    )
    llm = FakeLLM("Claude [M1].")
    use_llm(llm)

    client.post("/ask", json={"question": "Which LLM do we use?"})

    prompt = llm.calls[-1][1]
    assert "switched the LLM to Claude" in prompt
    assert "uses Gemini" not in prompt
