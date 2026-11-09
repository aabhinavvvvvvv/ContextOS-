"""API shapes for grounded answers."""

import uuid

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10, description="How many chunks to give the model")


class CitationRead(BaseModel):
    number: int  # matches the [n] in the answer text
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    heading: str | None
    page_number: int | None
    text: str


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: list[CitationRead]
    sources_considered: int
    input_tokens: int
    output_tokens: int
