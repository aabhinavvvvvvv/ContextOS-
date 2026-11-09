"""API shapes for conversations."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.answers.schemas import CitationRead


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    role: Literal["user", "assistant"]
    content: str
    citations: list[CitationRead]  # empty for user messages
    created_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead]
