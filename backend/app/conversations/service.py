"""Conversation storage: record each question-and-answer turn, read threads back.

Conversations are the raw material of memory: Phase 3's memory extraction reads them,
and every memory will point back to the message it came from (its provenance).
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message

TITLE_MAX_CHARS = 80


class ConversationNotFoundError(Exception):
    pass


def make_title(question: str) -> str:
    """The first question names the thread, shortened on a word boundary if it's long."""
    title = " ".join(question.split())  # collapse newlines and repeated spaces
    if len(title) <= TITLE_MAX_CHARS:
        return title
    return title[:TITLE_MAX_CHARS].rsplit(" ", 1)[0] + "…"


async def get_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


async def list_conversations(session: AsyncSession, *, limit: int) -> Sequence[Conversation]:
    """Most recently active first."""
    result = await session.scalars(
        select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)
    )
    return result.all()


async def list_messages(session: AsyncSession, conversation_id: uuid.UUID) -> Sequence[Message]:
    result = await session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.position)
    )
    return result.all()


async def record_turn(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID | None,
    question: str,
    answer: str,
    citations: list[dict[str, Any]],
) -> uuid.UUID:
    """Save a question and its answer together, in one transaction. Returns the conversation id.

    With no conversation_id, a new conversation is started.
    """
    if conversation_id is None:
        conversation = Conversation(id=uuid.uuid4(), title=make_title(question))
        session.add(conversation)
        next_position = 0
    else:
        # SELECT ... FOR UPDATE locks this conversation's row until we commit, so two turns
        # sent at the same moment take turns here instead of both picking the same position.
        conversation = await session.get(Conversation, conversation_id, with_for_update=True)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        next_position = await session.scalar(
            select(func.count()).where(Message.conversation_id == conversation.id)
        )
        conversation.updated_at = func.now()  # computed by Postgres at write time

    session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                position=next_position,
                role="user",
                content=question,
            ),
            Message(
                conversation_id=conversation.id,
                position=next_position + 1,
                role="assistant",
                content=answer,
                citations=citations,
            ),
        ]
    )
    # Read before commit: a session may expire objects on commit, and reloading an expired
    # attribute would be hidden I/O, which async SQLAlchemy forbids (MissingGreenlet).
    saved_id = conversation.id
    await session.commit()
    return saved_id
