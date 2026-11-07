"""API shapes for search results."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    heading: str | None
    page_number: int | None
    text: str
    score: float
    semantic_rank: int | None = None
    keyword_rank: int | None = None


class SearchResponse(BaseModel):
    query: str
    mode: Literal["hybrid", "semantic", "keyword"]
    results: list[SearchResult]
