"""API shapes for documents (what clients send and receive)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    # from_attributes=True: build this model from an ORM object's attributes (doc.id, doc.filename, ...).
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: Literal["pending", "processing", "ready", "failed"]
    error: str | None
    created_at: datetime
    updated_at: datetime
    source: Literal["upload", "github", "folder"] = "upload"
    source_url: str | None = None  # e.g. the GitHub issue page
    connector_id: uuid.UUID | None = None


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    text: str
    heading: str | None
    page_number: int | None
