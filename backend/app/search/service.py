"""Semantic search: the chunks whose embeddings are nearest to a query embedding."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentChunk


@dataclass(frozen=True)
class SearchHit:
    """One matching chunk, with everything needed to cite it."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    heading: str | None
    page_number: int | None
    text: str
    score: float  # cosine similarity: 1.0 = same meaning, ~0 = unrelated


async def semantic_search(
    session: AsyncSession, query_vector: list[float], *, limit: int
) -> list[SearchHit]:
    # `<=>` (cosine distance). "ORDER BY embedding <=> :query LIMIT n" is the exact shape
    # the HNSW index on chunks.embedding can serve.
    distance = DocumentChunk.embedding.cosine_distance(query_vector)
    rows = await session.execute(
        # Select columns, not whole DocumentChunk rows: that would also load 384 floats per hit.
        select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            Document.filename,
            DocumentChunk.chunk_index,
            DocumentChunk.heading,
            DocumentChunk.page_number,
            DocumentChunk.text,
            distance.label("distance"),
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .order_by(distance)
        .limit(limit)
    )
    return [
        SearchHit(
            chunk_id=row.id,
            document_id=row.document_id,
            filename=row.filename,
            chunk_index=row.chunk_index,
            heading=row.heading,
            page_number=row.page_number,
            text=row.text,
            score=1 - row.distance,
        )
        for row in rows
    ]
