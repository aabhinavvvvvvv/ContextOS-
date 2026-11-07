"""HTTP routes for search."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from app.db import SessionDep
from app.embeddings import Embedder, get_embedder
from app.search import service
from app.search.schemas import SearchResponse, SearchResult

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
async def search(
    session: SessionDep,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    q: Annotated[str, Query(min_length=1, max_length=500, description="What to look for")],
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
    mode: Literal["semantic", "keyword"] = "semantic",
) -> SearchResponse:
    """Search chunks, best first.

    - `semantic`: closest in meaning to `q` (score = cosine similarity, 0–1)
    - `keyword`: contains the words of `q`, stemmed; supports "quoted phrases", `or`,
      `-exclude` (score = ts_rank, a different scale; don't compare across modes)
    """
    if mode == "keyword":
        hits = await service.keyword_search(session, q, limit=limit)
    else:
        # Embedding is CPU work; a worker thread keeps the event loop free for other requests.
        query_vector = await run_in_threadpool(embedder.embed_query, q)
        hits = await service.semantic_search(session, query_vector, limit=limit)
    return SearchResponse(
        query=q, mode=mode, results=[SearchResult.model_validate(hit) for hit in hits]
    )
