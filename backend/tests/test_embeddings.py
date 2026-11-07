import math

import pytest

from app.embeddings import EMBEDDING_DIM, Embedder, chunk_embedding_text


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_chunk_embedding_text_prepends_heading_path() -> None:
    assert chunk_embedding_text("Stack > Database", "PostgreSQL") == "Stack > Database\n\nPostgreSQL"
    assert chunk_embedding_text(None, "PostgreSQL") == "PostgreSQL"


@pytest.mark.model
def test_vectors_have_model_dimension_and_unit_length(real_embedder: Embedder) -> None:
    [vector] = real_embedder.embed_passages(["We chose REST over GraphQL."])

    assert len(vector) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, abs_tol=1e-3)


@pytest.mark.model
def test_query_is_closest_to_the_passage_with_the_same_meaning(real_embedder: Embedder) -> None:
    database, frontend, groceries = real_embedder.embed_passages(
        [
            chunk_embedding_text("Technology Stack > Database and Storage", "PostgreSQL, pgvector, Redis"),
            chunk_embedding_text("Technology Stack > Frontend", "React, TypeScript, Tailwind CSS"),
            "Buy milk, eggs and bread on the way home.",
        ]
    )
    query = real_embedder.embed_query("Which database stores the vectors?")

    assert cosine(query, database) > cosine(query, frontend) > cosine(query, groceries)
