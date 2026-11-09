"""HTTP routes for reading conversations (they are written by POST /ask)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.conversations import service
from app.conversations.schemas import ConversationDetail, ConversationRead, MessageRead
from app.db import SessionDep

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[ConversationRead]:
    """List conversations, most recently active first."""
    conversations = await service.list_conversations(session, limit=limit)
    return [ConversationRead.model_validate(c) for c in conversations]


@router.get("/{conversation_id}")
async def read_conversation(conversation_id: uuid.UUID, session: SessionDep) -> ConversationDetail:
    """One conversation with all its messages, in order."""
    conversation = await service.get_conversation(session, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    messages = await service.list_messages(session, conversation_id)
    return ConversationDetail(
        **ConversationRead.model_validate(conversation).model_dump(),
        messages=[MessageRead.model_validate(m) for m in messages],
    )
