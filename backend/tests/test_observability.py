"""Observability: LLM calls and HTTP requests are recorded and summarised by GET /metrics."""

import pytest
from fastapi.testclient import TestClient

from app.llm import LLMRateLimitError
from tests.helpers import FakeLLM
from tests.test_agent import NO_MORE_STEPS, plan_json
from tests.test_answers import upload, use_llm


def metrics(client: TestClient) -> dict:
    response = client.get("/metrics")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.integration
def test_llm_calls_are_recorded_per_purpose_with_tokens_and_cost(client: TestClient) -> None:
    upload(client)
    use_llm(FakeLLM([plan_json(("Do we use FastAPI?", "search"), ("Any tasks?", "list_tasks")), NO_MORE_STEPS, "FastAPI [1]."]))

    client.post("/ask", json={"question": "Do we use FastAPI, and any tasks?"})

    usage = {u["purpose"]: u for u in metrics(client)["llm"]}
    assert set(usage) == {"plan", "check", "answer"}
    assert (usage["answer"]["calls"], usage["answer"]["input_tokens"], usage["answer"]["output_tokens"]) == (1, 100, 10)
    # Default example prices: $0.10 / $0.40 per million tokens → 100*0.10/1e6 + 10*0.40/1e6
    assert usage["answer"]["estimated_cost_usd"] == pytest.approx(0.000014)
    assert metrics(client)["total_estimated_cost_usd"] == pytest.approx(3 * 0.000014)


@pytest.mark.integration
def test_llm_errors_are_counted_and_listed(client: TestClient) -> None:
    upload(client)
    use_llm(FakeLLM(error=LLMRateLimitError("quota used up")))

    assert client.post("/ask", json={"question": "Which framework do we use?"}).status_code == 429

    body = metrics(client)
    [answer] = body["llm"]
    assert (answer["purpose"], answer["calls"], answer["errors"]) == ("answer", 1, 1)
    assert body["recent_errors"][0]["source"] == "llm:answer"
    assert "quota used up" in body["recent_errors"][0]["error"]


@pytest.mark.integration
def test_requests_are_grouped_by_route_template(client: TestClient) -> None:
    client.get("/documents/00000000-0000-0000-0000-000000000001")  # 404
    client.get("/documents/00000000-0000-0000-0000-000000000002")  # 404
    client.get("/health")  # not recorded

    endpoints = {(e["method"], e["path"]): e for e in metrics(client)["endpoints"]}
    assert endpoints[("GET", "/documents/{document_id}")]["requests"] == 2
    assert endpoints[("GET", "/documents/{document_id}")]["server_errors"] == 0  # 404 is not a 5xx
    assert not any(path.startswith("/health") for _, path in endpoints)
