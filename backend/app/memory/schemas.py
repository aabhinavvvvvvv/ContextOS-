"""API shapes for long-term memory."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.memory.extraction import MAX_MEMORY_CHARS
from app.models import MemoryKind


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: MemoryKind
    content: str
    created_at: datetime
    # Provenance: the user message (and its conversation) this memory was extracted from.
    source_message_id: uuid.UUID
    conversation_id: uuid.UUID
    # Null while current; otherwise the newer memory that replaced this one, and when.
    superseded_by_id: uuid.UUID | None
    superseded_at: datetime | None
    # Time-bound memories only: the last day it is true.
    expires_on: date | None


class MemoryEdit(BaseModel):
    # pattern: at least one non-space character, so "   " can't become an empty memory.
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CHARS, pattern=r"\S")
