"""Query decomposition: a multi-part question is split into sub-questions, each searched."""

import json

import pytest
from fastapi.testclient import TestClient

from app.answers.service import (
    PLAN_SYSTEM_PROMPT,
    looks_multi_part,
    parse_plan,
    question_with_parts,
)
from tests.helpers import FakeLLM
from tests.test_answers import upload, use_llm

# --- pure functions -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Which LLM do we use?", False),
        ("Why?", False),
        ("What did we decide about the API and what is unfinished?", True),
        ("What did we decide about the API? What is unfinished?", True),
        ("Who owns the backend, and when is the demo?", True),
        ("Branding guidelines; logo colours", True),
    ],
)
def test_looks_multi_part(question: str, expected: bool) -> None:
    assert looks_multi_part(question) is expected


def test_parse_plan_cleans_and_caps_the_sub_questions() -> None:
    raw = json.dumps(
        {"sub_questions": ["  What did we   decide? ", "", "What did we decide?", "A?", "B?", "C?"]}
    )

    # whitespace tidied, empty and repeated dropped, at most 3
    assert parse_plan(raw, "original") == ["What did we decide?", "A?", "B?"]


@pytest.mark.parametrize("raw", ["not json", '{"sub_questions": []}', '{"sub_questions": ["", " "]}'])
def test_parse_plan_falls_back_to_the_question(raw: str) -> None:
    assert parse_plan(raw, "original question") == ["original question"]


def test_question_with_parts_lists_them_only_when_there_are_several() -> None:
    assert question_with_parts("Q?", ["Q?"]) == "Q?"
    assert question_with_parts("A and B?", ["A?", "B?"]) == "A and B?\n\nAnswer each part:\n- A?\n- B?"


# --- POST /ask --------------------------------------------------------------------------


@pytest.mark.integration
def test_a_multi_part_question_is_searched_part_by_part(client: TestClient) -> None:
    upload(client)  # two chunks: "We use FastAPI." and "REST, not GraphQL."
    plan = {"sub_questions": ["Do we use FastAPI?", "Do we use GraphQL?"]}
    llm = FakeLLM([json.dumps(plan), "FastAPI [1], and REST rather than GraphQL [2]."])
    use_llm(llm)

    body = client.post(
        "/ask", json={"question": "Do we use FastAPI, and GraphQL?", "top_k": 1}
    ).json()

    assert body["sub_questions"] == plan["sub_questions"]
    assert llm.calls[0][0] == PLAN_SYSTEM_PROMPT and len(llm.json_schemas) == 1
    # top_k=1 per sub-question, and each found its own chunk: two sources in total.
    assert body["sources_considered"] == 2
    answer_prompt = llm.calls[1][1]
    assert "We use FastAPI." in answer_prompt and "REST, not GraphQL." in answer_prompt
    assert "Answer each part:\n- Do we use FastAPI?\n- Do we use GraphQL?" in answer_prompt
    assert (body["input_tokens"], body["output_tokens"]) == (200, 20)  # plan + answer


@pytest.mark.integration
def test_a_single_question_skips_planning(client: TestClient) -> None:
    upload(client)
    llm = FakeLLM("FastAPI [1].")
    use_llm(llm)

    body = client.post("/ask", json={"question": "Which framework do we use?"}).json()

    assert len(llm.calls) == 1 and llm.json_schemas == []  # only the answer call
    assert body["sub_questions"] == []
