"""API shapes for long-term memory."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

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
