"""Search over chunks.

- semantic_search: nearest embeddings to the query embedding (meaning; good at paraphrases)
- keyword_search: Postgres full-text search (exact words, stemmed; good at names and terms)

Their scores are on different scales and must not be compared or added directly.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, cast, func, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TEXT_SEARCH_CONFIG, Document, DocumentChunk

# Columns every search returns: what's needed to show and cite a chunk.
_HIT_COLUMNS = (
    DocumentChunk.id,
    DocumentChunk.document_id,
    Document.filename,
    DocumentChunk.chunk_index,
    DocumentChunk.heading,
    DocumentChunk.page_number,
    DocumentChunk.text,
)


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
    # Semantic: cosine similarity (1.0 = same meaning). Keyword: ts_rank (unbounded, small).
    score: float


async def semantic_search(
    session: AsyncSession, query_vector: list[float], *, limit: int
) -> list[SearchHit]:
    # `<=>` (cosine distance). "ORDER BY embedding <=> :query LIMIT n" is the exact shape
    # the HNSW index on chunks.embedding can serve.
    distance = DocumentChunk.embedding.cosine_distance(query_vector)
    return await _run(session, score=1 - distance, order_by=[distance], limit=limit)


async def keyword_search(session: AsyncSession, query: str, *, limit: int) -> list[SearchHit]:
    # websearch_to_tsquery understands "quoted phrases", or, and -exclusions, and never
    # raises on odd input. A query of only stop words ("the a of") matches nothing.
    ts_query = func.websearch_to_tsquery(cast(TEXT_SEARCH_CONFIG, REGCONFIG), query)
    rank = func.ts_rank(DocumentChunk.search_vector, ts_query)
    return await _run(
        session,
        score=rank,
        # Equal ranks are common (one mention each), so id breaks ties deterministically.
        order_by=[rank.desc(), DocumentChunk.id],
        where=DocumentChunk.search_vector.op("@@")(ts_query),  # served by the GIN index
        limit=limit,
    )


async def _run(
    session: AsyncSession,
    *,
    score: ColumnElement[float],
    order_by: list[ColumnElement],
    limit: int,
    where: ColumnElement[bool] | None = None,
) -> list[SearchHit]:
    # Select columns, not whole DocumentChunk rows (embedding and search_vector are large).
    query = (
        select(*_HIT_COLUMNS, score.label("score"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .order_by(*order_by)
        .limit(limit)
    )
    if where is not None:
        query = query.where(where)
    rows = await session.execute(query)
    return [
        SearchHit(
            chunk_id=row.id,
            document_id=row.document_id,
            filename=row.filename,
            chunk_index=row.chunk_index,
            heading=row.heading,
            page_number=row.page_number,
            text=row.text,
            score=row.score,
        )
        for row in rows
    ]
