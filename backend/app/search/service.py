"""Search over chunks.

- semantic_search: nearest embeddings to the query embedding (meaning; good at paraphrases)
- keyword_search: Postgres full-text search (exact words, stemmed; good at names and terms)
- hybrid_search: both, merged by Reciprocal Rank Fusion (each covers the other's blind spot)

Semantic and keyword scores are on different scales and must not be compared or added
directly; RRF uses only rank positions.
"""

import uuid
from dataclasses import dataclass, replace

from sqlalchemy import ColumnElement, cast, func, select, text
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TEXT_SEARCH_CONFIG, Document, DocumentChunk

_DEFAULT_EF_SEARCH = 40  # pgvector's default

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
    # Hybrid: RRF score (sum of 1 / (k + rank)).
    score: float
    # Hybrid only: this chunk's position (1 = best) in each method's list, None if absent.
    semantic_rank: int | None = None
    keyword_rank: int | None = None


RRF_K = 60  # from the original RRF paper (Cormack et al., 2009); the usual default


def reciprocal_rank_fusion(
    semantic: list[SearchHit], keyword: list[SearchHit], *, k: int = RRF_K
) -> list[SearchHit]:
    """Merge two rankings by position only: score = sum of 1 / (k + rank) over the lists.

    A chunk found by both methods outranks one ranked first by a single method.
    Equal scores keep semantic-first order (Python's sort is stable).
    """
    fused: dict[uuid.UUID, SearchHit] = {}
    for rank, hit in enumerate(semantic, start=1):
        fused[hit.chunk_id] = replace(hit, score=1 / (k + rank), semantic_rank=rank)
    for rank, hit in enumerate(keyword, start=1):
        existing = fused.get(hit.chunk_id)
        if existing is None:
            fused[hit.chunk_id] = replace(hit, score=1 / (k + rank), keyword_rank=rank)
        else:
            fused[hit.chunk_id] = replace(
                existing, score=existing.score + 1 / (k + rank), keyword_rank=rank
            )
    return sorted(fused.values(), key=lambda hit: hit.score, reverse=True)


async def hybrid_search(
    session: AsyncSession, query: str, query_vector: list[float], *, limit: int
) -> list[SearchHit]:
    # Fetch extra candidates from each method: a chunk ranked #15 by one method and #2 by
    # the other can still earn a place in the final top `limit`.
    candidates = max(limit * 4, 20)
    semantic = await semantic_search(session, query_vector, limit=candidates)
    keyword = await keyword_search(session, query, limit=candidates)
    return reciprocal_rank_fusion(semantic, keyword)[:limit]


async def semantic_search(
    session: AsyncSession, query_vector: list[float], *, limit: int
) -> list[SearchHit]:
    # An HNSW index scan returns at most hnsw.ef_search rows (default 40), silently ignoring
    # a larger LIMIT. SET LOCAL raises it for this transaction only. SET can't take bind
    # parameters, so the value is formatted in; it's an int we computed, not user text.
    ef_search = max(limit, _DEFAULT_EF_SEARCH)
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))

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
