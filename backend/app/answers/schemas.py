"""API shapes for grounded answers."""

import uuid

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10, description="How many chunks to give the model")
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Continue this conversation; omit to start a new one"
    )


class CitationRead(BaseModel):
    number: int  # matches the [n] in the answer text
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    heading: str | None
    page_number: int | None
    text: str


class AskResponse(BaseModel):
    conversation_id: uuid.UUID  # send it back with the next question to continue the thread
    question: str
    answer: str
    citations: list[CitationRead]
    sources_considered: int
    input_tokens: int
    output_tokens: int
