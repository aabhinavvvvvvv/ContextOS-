import uuid

import pytest
from fastapi.testclient import TestClient

from app.answers.service import NOT_FOUND_ANSWER, SYSTEM_PROMPT, build_prompt, cited_sources
from app.config import get_settings
from app.llm import LLMError, LLMNotConfiguredError, LLMRateLimitError, get_llm
from app.main import app
from app.search.service import SearchHit
from tests.helpers import FakeLLM

NOTES = b"# Architecture\nWe use FastAPI.\n\n## API\nREST, not GraphQL.\n"


def hit(text: str, *, heading: str | None = None, page: int | None = None, filename: str = "doc.md") -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        filename=filename,
        chunk_index=0,
        heading=heading,
        page_number=page,
        text=text,
        score=0.0,
    )


# --- pure functions: no database, no model ---------------------------------------


def test_build_prompt_numbers_and_delimits_each_source() -> None:
    prompt = build_prompt(
        "Which API style?",
        [
            hit("REST, not GraphQL.", heading="Architecture > API"),
            hit("Decision: REST", page=2, filename="adr.pdf"),
        ],
    )

    assert prompt == (
        "Sources:\n\n"
        '<source id="1" document="doc.md" location="Architecture > API">\nREST, not GraphQL.\n</source>\n\n'
        '<source id="2" document="adr.pdf" location="page 2">\nDecision: REST\n</source>\n\n'
        "Question: Which API style?\n\n"
        "Answer using only the sources above, citing them like [1]."
    )


def test_cited_sources_are_verified_deduplicated_and_in_first_cited_order() -> None:
    hits = [hit("a"), hit("b"), hit("c")]

    citations = cited_sources("B says so [2]. A agrees [1][2]. Ignore [9] and [0].", hits)

    # [9] and [0] don't match any source, the second [2] is a repeat, [3] was never cited.
    assert [(c.number, c.hit.text) for c in citations] == [(2, "b"), (1, "a")]


def test_get_llm_without_an_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    get_llm.cache_clear()
    try:
        with pytest.raises(LLMNotConfiguredError, match="GEMINI_API_KEY"):
            get_llm()
    finally:
        get_settings.cache_clear()
        get_llm.cache_clear()


# --- POST /ask: retrieval + fake LLM ---------------------------------------------


def upload(client: TestClient, content: bytes = NOTES) -> None:
    assert client.post("/documents", files={"file": ("notes.md", content)}).status_code == 201


def use_llm(llm: object) -> None:
    app.dependency_overrides[get_llm] = lambda: llm


@pytest.mark.integration
def test_ask_answers_from_retrieved_sources_with_verified_citations(client: TestClient) -> None:
    upload(client)
    llm = FakeLLM("We use REST [1]. See also [7].")
    use_llm(llm)

    response = client.post("/ask", json={"question": "REST GraphQL", "top_k": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "We use REST [1]. See also [7]."
    # [7] doesn't exist (only 2 sources), so only [1] becomes a citation.
    assert [c["number"] for c in body["citations"]] == [1]
    assert body["citations"][0]["heading"] == "Architecture > API"
    assert body["citations"][0]["text"] == "REST, not GraphQL."
    assert body["sources_considered"] == 2
    # What was actually sent to the model:
    [(system, prompt)] = llm.calls
    assert system == SYSTEM_PROMPT
    assert '<source id="1" document="notes.md" location="Architecture > API">' in prompt
    assert "\n\nQuestion: REST GraphQL\n\n" in prompt


@pytest.mark.integration
def test_ask_with_no_documents_says_not_found_without_calling_the_llm(client: TestClient) -> None:
    llm = FakeLLM()
    use_llm(llm)

    body = client.post("/ask", json={"question": "What did we decide?"}).json()

    assert body["answer"] == NOT_FOUND_ANSWER
    assert body["citations"] == []
    assert llm.calls == []  # zero quota spent, zero chance to hallucinate


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "status_code"),
    [(LLMRateLimitError("quota used up"), 429), (LLMError("upstream failed"), 502)],
)
def test_llm_failures_become_clear_status_codes(
    client: TestClient, error: Exception, status_code: int
) -> None:
    upload(client)
    use_llm(FakeLLM(error=error))

    assert client.post("/ask", json={"question": "REST"}).status_code == status_code


@pytest.mark.integration
def test_missing_api_key_returns_503_with_instructions(client: TestClient) -> None:
    def not_configured() -> None:
        raise LLMNotConfiguredError("No LLM configured: set GEMINI_API_KEY in .env")

    app.dependency_overrides[get_llm] = not_configured

    response = client.post("/ask", json={"question": "anything"})

    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json()["detail"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "payload",
    [{}, {"question": ""}, {"question": "x" * 1001}, {"question": "ok", "top_k": 0}, {"question": "ok", "top_k": 11}],
)
def test_invalid_ask_requests_return_422(client: TestClient, payload: dict) -> None:
    assert client.post("/ask", json=payload).status_code == 422
