"""Working memory: follow-up questions are rewritten into standalone questions before search."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.answers.service import (
    MAX_HISTORY_MESSAGE_CHARS,
    REWRITE_SYSTEM_PROMPT,
    build_rewrite_prompt,
    standalone_question,
)
from app.llm import get_llm
from app.main import app
from tests.helpers import FakeLLM

NOTES = b"# Architecture\nWe use FastAPI.\n\n## API\nREST, not GraphQL.\n"

HISTORY = [("user", "What did we decide?"), ("assistant", "We moved ingestion\nto a worker [1][2].")]


# --- pure functions ---------------------------------------------------------------


def test_rewrite_prompt_shows_the_conversation_without_old_citation_numbers() -> None:
    prompt = build_rewrite_prompt("Why?", HISTORY)

    # [1][2] numbered the sources of an EARLIER prompt; left in, they'd be confused with
    # the numbering of the new sources.
    assert prompt == (
        "Conversation:\n"
        "User: What did we decide?\n"
        "Assistant: We moved ingestion to a worker.\n\n"
        "Follow-up question: Why?"
    )


def test_rewrite_prompt_clips_long_messages() -> None:
    prompt = build_rewrite_prompt("Why?", [("assistant", "x" * 5000)])

    assert f"Assistant: {'x' * MAX_HISTORY_MESSAGE_CHARS}…\n" in prompt


# --- standalone_question with a fake LLM ------------------------------------------


def rewrite(llm: FakeLLM, question: str, history: list[tuple[str, str]]) -> str:
    return asyncio.run(standalone_question(llm, question, history)).question


def test_first_question_is_not_rewritten_and_costs_no_llm_call() -> None:
    llm = FakeLLM()

    assert rewrite(llm, "What did we decide?", []) == "What did we decide?"
    assert llm.calls == []


def test_follow_up_is_rewritten_by_the_llm() -> None:
    llm = FakeLLM('  "Why did we move ingestion to a worker?"\n')

    assert rewrite(llm, "Why?", HISTORY) == "Why did we move ingestion to a worker?"
    [(system, prompt)] = llm.calls
    assert system == REWRITE_SYSTEM_PROMPT
    assert prompt.endswith("Follow-up question: Why?")


@pytest.mark.parametrize("bad_output", ["", "   ", "x" * 1001])
def test_unusable_rewrite_falls_back_to_the_original_question(bad_output: str) -> None:
    assert rewrite(FakeLLM(bad_output), "Why?", HISTORY) == "Why?"


# --- POST /ask with a conversation ------------------------------------------------


def ask(client: TestClient, question: str, conversation_id: str | None = None) -> dict:
    response = client.post("/ask", json={"question": question, "conversation_id": conversation_id})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.integration
def test_follow_up_searches_and_answers_the_standalone_question(client: TestClient) -> None:
    assert client.post("/documents", files={"file": ("notes.md", NOTES)}).status_code == 201
    app.dependency_overrides[get_llm] = lambda: FakeLLM("We use REST [1].")
    first = ask(client, "Which API style?")
    assert first["standalone_question"] is None  # nothing to resolve on a first question

    llm = FakeLLM(["Why did we choose REST over GraphQL?", "Because of the notes [1]."])
    app.dependency_overrides[get_llm] = lambda: llm
    body = ask(client, "Why?", first["conversation_id"])

    assert body["standalone_question"] == "Why did we choose REST over GraphQL?"
    assert body["answer"] == "Because of the notes [1]."
    (rewrite_system, rewrite_prompt), (_, answer_prompt) = llm.calls
    assert rewrite_system == REWRITE_SYSTEM_PROMPT
    assert "User: Which API style?\nAssistant: We use REST.\n" in rewrite_prompt
    assert "Question: Why did we choose REST over GraphQL?" in answer_prompt
    # Token usage covers both calls.
    assert (body["input_tokens"], body["output_tokens"]) == (200, 20)
    # The conversation keeps what the user actually typed.
    messages = client.get(f"/conversations/{first['conversation_id']}").json()["messages"]
    assert messages[2]["content"] == "Why?"


@pytest.mark.integration
def test_only_the_last_three_turns_are_sent(client: TestClient) -> None:
    conversation_id = ask(client, "q1")["conversation_id"]
    for question in ["q2", "q3", "q4"]:
        ask(client, question, conversation_id)
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm

    ask(client, "q5", conversation_id)

    rewrite_prompt = llm.calls[0][1]
    assert "User: q1\n" not in rewrite_prompt
    assert all(f"User: {q}\n" in rewrite_prompt for q in ["q2", "q3", "q4"])
