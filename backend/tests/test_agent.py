"""The answer agent: planning, tools, the evidence-check loop, failure handling, timeline."""

import json

import pytest
from fastapi.testclient import TestClient

from app.answers import tools
from app.answers.planning import (
    CHECK_SYSTEM_PROMPT,
    PLAN_SYSTEM_PROMPT,
    needs_planning,
    parse_steps,
    step_key,
)
from app.answers.service import question_with_parts
from app.llm import LLMError, LLMResult
from tests.helpers import FakeLLM
from tests.test_answers import upload, use_llm
from tests.test_memory_management import store_with


def plan_json(*steps: tuple[str, str]) -> str:
    return json.dumps({"steps": [{"question": q, "tool": t} for q, t in steps]})


NO_MORE_STEPS = json.dumps({"steps": []})

# --- pure functions -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Which LLM do we use?", False),
        ("Why?", False),
        ("What did we decide about the API and what is unfinished?", True),
        ("What did we decide about the API? What is unfinished?", True),
        ("Who owns the backend, and when is the demo?", True),
        ("What tasks are open?", True),  # needs list_tasks, not plain search
        ("What should I work on today?", True),
    ],
)
def test_needs_planning(question: str, expected: bool) -> None:
    assert needs_planning(question) is expected


def test_parse_steps_cleans_dedupes_and_caps() -> None:
    raw = plan_json(
        ("  What did we   decide? ", "search"),
        ("", "search"),
        ("What did we decide?", "search"),  # repeat
        ("What did we decide?", "list_tasks"),  # same question, other tool: kept
        ("B?", "search"),
        ("C?", "search"),
    )

    steps = parse_steps(raw, limit=3)

    assert [(s.question, s.tool) for s in steps] == [
        ("What did we decide?", "search"),
        ("What did we decide?", "list_tasks"),
        ("B?", "search"),
    ]


def test_parse_steps_skips_steps_already_tried_and_rejects_unknown_tools() -> None:
    tried = [step_key("search", "What did we decide?")]
    assert parse_steps(plan_json(("what did we  decide?", "search")), limit=2, tried=tried) == []
    with pytest.raises(ValueError):  # pydantic ValidationError is a ValueError
        parse_steps(plan_json(("Delete everything", "drop_database")), limit=2)


def test_question_with_parts_lists_them_only_when_there_are_several() -> None:
    assert question_with_parts("Q?", ["Q?"]) == "Q?"
    assert question_with_parts("A and B?", ["A?", "B?"]) == "A and B?\n\nAnswer each part:\n- A?\n- B?"


# --- POST /ask through the agent ------------------------------------------------------


def ask(client: TestClient, question: str, top_k: int = 5) -> dict:
    response = client.post("/ask", json={"question": question, "top_k": top_k})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.integration
def test_a_simple_question_takes_the_fast_path(client: TestClient) -> None:
    upload(client)
    llm = FakeLLM("FastAPI [1].")
    use_llm(llm)

    body = ask(client, "Which framework do we use?")

    assert len(llm.calls) == 1 and llm.json_schemas == []  # only the answer call
    assert body["sub_questions"] == []
    assert [e["step"] for e in body["timeline"]] == ["plan", "tool:search", "generate"]


@pytest.mark.integration
def test_a_multi_part_question_runs_one_search_per_part(client: TestClient) -> None:
    upload(client)  # two chunks: "We use FastAPI." and "REST, not GraphQL."
    plan = plan_json(("Do we use FastAPI?", "search"), ("Do we use GraphQL?", "search"))
    llm = FakeLLM([plan, NO_MORE_STEPS, "FastAPI [1], and REST rather than GraphQL [2]."])
    use_llm(llm)

    body = ask(client, "Do we use FastAPI, and GraphQL?", top_k=1)

    assert llm.calls[0][0] == PLAN_SYSTEM_PROMPT and llm.calls[1][0] == CHECK_SYSTEM_PROMPT
    assert body["sub_questions"] == ["Do we use FastAPI?", "Do we use GraphQL?"]
    assert body["sources_considered"] == 2  # top_k=1 per search, each found its own chunk
    answer_prompt = llm.calls[2][1]
    assert "We use FastAPI." in answer_prompt and "REST, not GraphQL." in answer_prompt
    assert "Answer each part:\n- Do we use FastAPI?\n- Do we use GraphQL?" in answer_prompt
    assert (body["input_tokens"], body["output_tokens"]) == (300, 30)  # plan + check + answer
    assert [e["step"] for e in body["timeline"]] == [
        "plan", "tool:search", "tool:search", "check", "generate",
    ]


@pytest.mark.integration
def test_list_tasks_returns_every_open_task(client: TestClient) -> None:
    for i in range(5):
        store_with(client, {"kind": "task", "content": f"The user needs to do task number {i}."})
    store_with(client, {"kind": "fact", "content": "The user is a student."})
    llm = FakeLLM([plan_json(("What tasks are unfinished?", "list_tasks")), NO_MORE_STEPS, "Five [M1]."])
    use_llm(llm)

    ask(client, "What tasks are unfinished?")

    answer_prompt = llm.calls[-1][1]
    assert all(f"task number {i}." in answer_prompt for i in range(5))  # all, not the top 3
    assert "student" not in answer_prompt


@pytest.mark.integration
def test_the_check_can_add_one_more_round_and_then_stops(client: TestClient) -> None:
    upload(client)
    llm = FakeLLM(
        [
            plan_json(("Do we use FastAPI?", "search"), ("What is open?", "list_tasks")),
            plan_json(("Do we use GraphQL?", "search")),  # the check asks for one more search
            "Answer [1][2].",
        ]
    )
    use_llm(llm)

    body = ask(client, "Do we use FastAPI, and what is open?", top_k=1)

    # plan, check, answer: no second check, because the round limit (2) was reached
    assert [system for system, _ in llm.calls][:2] == [PLAN_SYSTEM_PROMPT, CHECK_SYSTEM_PROMPT]
    assert len(llm.calls) == 3
    steps = [e["step"] for e in body["timeline"]]
    assert steps == ["plan", "tool:search", "tool:list_tasks", "check", "tool:search", "generate"]
    assert "REST, not GraphQL." in llm.calls[-1][1]  # found in the second round


class FailingPlannerLLM(FakeLLM):
    """Planning and checking (the JSON calls) fail; answering works."""

    async def generate(self, *, system: str, prompt: str, json_schema: dict | None = None, **kwargs) -> LLMResult:
        if json_schema is not None:
            self.calls.append((system, prompt))
            raise LLMError("planner down")
        return await super().generate(system=system, prompt=prompt, **kwargs)


@pytest.mark.integration
def test_a_failed_plan_falls_back_to_one_search(client: TestClient) -> None:
    upload(client)
    use_llm(FailingPlannerLLM("FastAPI [1]."))

    body = ask(client, "Do we use FastAPI, and GraphQL?")

    assert body["answer"] == "FastAPI [1]."
    plan_event = body["timeline"][0]
    assert plan_event["step"] == "plan" and plan_event["error"] == "planner down"


@pytest.mark.integration
def test_a_failing_tool_is_skipped_not_fatal(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    upload(client)

    async def broken(question: str, ctx: tools.ToolContext) -> tools.ToolResult:
        raise RuntimeError("tool exploded")

    monkeypatch.setitem(tools.TOOLS, "list_tasks", broken)
    llm = FakeLLM(
        [plan_json(("Do we use FastAPI?", "search"), ("What is open?", "list_tasks")), NO_MORE_STEPS, "FastAPI [1]."]
    )
    use_llm(llm)

    body = ask(client, "Do we use FastAPI, and what is open?")

    assert body["answer"] == "FastAPI [1]."
    failed = [e for e in body["timeline"] if e["error"]]
    assert [(e["step"], e["error"]) for e in failed] == [("tool:list_tasks", "RuntimeError: tool exploded")]
