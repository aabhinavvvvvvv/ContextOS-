import pytest
from fastapi.testclient import TestClient

from app.embeddings import Embedder, get_embedder
from app.jobs import get_job_queue
from app.main import app
from tests.helpers import InlineJobQueue

pytestmark = pytest.mark.integration

NOTES = b"""# Architecture
We use FastAPI.

## API
REST, not GraphQL.
"""


def upload(client: TestClient, filename: str, content: bytes) -> dict:
    response = client.post("/documents", files={"file": (filename, content)})
    assert response.status_code == 201
    return response.json()


def search(client: TestClient, q: str, limit: int = 5) -> list[dict]:
    response = client.get("/search", params={"q": q, "limit": limit})
    assert response.status_code == 200
    return response.json()["results"]


def test_search_returns_the_matching_chunk_with_citation_fields(client: TestClient) -> None:
    document = upload(client, "notes.md", NOTES)

    # FakeEmbedder gives identical text an identical vector, so querying with a chunk's
    # exact embedding text (heading + text) must return that chunk first, similarity 1.
    [hit] = search(client, "Architecture > API\n\nREST, not GraphQL.", limit=1)

    assert hit["document_id"] == document["id"]
    assert hit["filename"] == "notes.md"
    assert hit["chunk_index"] == 1
    assert hit["heading"] == "Architecture > API"
    assert hit["page_number"] is None
    assert hit["text"] == "REST, not GraphQL."
    assert hit["score"] == pytest.approx(1.0, abs=1e-5)


def test_results_are_best_first_and_respect_the_limit(client: TestClient) -> None:
    upload(client, "notes.md", NOTES)
    upload(client, "more.md", b"# One\nfirst\n\n# Two\nsecond\n\n# Three\nthird\n")

    results = search(client, "anything", limit=3)

    scores = [hit["score"] for hit in results]
    assert len(results) == 3
    assert scores == sorted(scores, reverse=True)


def test_search_with_no_documents_returns_empty_results(client: TestClient) -> None:
    assert search(client, "anything") == []


def keyword(client: TestClient, q: str) -> list[dict]:
    response = client.get("/search", params={"q": q, "mode": "keyword"})
    assert response.status_code == 200
    assert response.json()["mode"] == "keyword"
    return response.json()["results"]


def test_keyword_search_matches_word_stems(client: TestClient) -> None:
    upload(client, "ops.md", b"# Operations\nThe worker runs ingestion jobs.\n\n# UI\nReact components.\n")

    # "workers" -> worker, "running" -> run: different words, same stems.
    assert [hit["heading"] for hit in keyword(client, "workers running")] == ["Operations"]


def test_keyword_matches_in_the_heading_rank_higher(client: TestClient) -> None:
    upload(
        client,
        "db.md",
        b"# Notes\nWe chose pgvector for storage.\n\n# pgvector\nIt adds a vector type.\n",
    )

    # Heading words are weighted A, body words B.
    assert [hit["heading"] for hit in keyword(client, "pgvector")] == ["pgvector", "Notes"]


def test_keyword_search_supports_exclusions_and_phrases(client: TestClient) -> None:
    upload(client, "api.md", b"# API\nREST, not GraphQL.\n\n# Cache\nREST responses are cached.\n")

    assert [hit["heading"] for hit in keyword(client, "rest -graphql")] == ["Cache"]
    assert [hit["heading"] for hit in keyword(client, '"responses are cached"')] == ["Cache"]


def test_keyword_search_of_only_stop_words_matches_nothing(client: TestClient) -> None:
    upload(client, "notes.md", NOTES)

    assert keyword(client, "the and of") == []


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "x" * 501},
        {"q": "ok", "limit": 0},
        {"q": "ok", "limit": 51},
        {"q": "ok", "mode": "fuzzy"},
    ],
)
def test_invalid_search_parameters_return_422(client: TestClient, params: dict) -> None:
    assert client.get("/search", params=params).status_code == 422


@pytest.mark.model
def test_search_finds_chunks_by_meaning_not_shared_words(
    client: TestClient, real_embedder: Embedder
) -> None:
    app.dependency_overrides[get_embedder] = lambda: real_embedder
    app.dependency_overrides[get_job_queue] = lambda: InlineJobQueue(
        app.state.db_engine, real_embedder
    )
    upload(
        client,
        "stack.md",
        b"# Database\nWe store everything in PostgreSQL with pgvector.\n\n"
        b"# Frontend\nThe UI is built with React and TypeScript.\n\n"
        b"# Errands\nBuy milk and eggs on the way home.\n",
    )

    # No word of the question appears in the Database chunk.
    results = search(client, "Where is our information kept?")

    assert [hit["heading"] for hit in results] == ["Database", "Frontend", "Errands"]
