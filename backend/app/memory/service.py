"""Long-term (semantic) memory: extract memories from a recorded turn and store them."""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.concurrency import run_in_threadpool

from app.embeddings import Embedder
from app.llm import LLM
from app.memory.extraction import extract_memories
from app.models import Memory, Message

logger = logging.getLogger(__name__)


async def remember_turn(
    engine: AsyncEngine, llm: LLM, embedder: Embedder, user_message_id: uuid.UUID
) -> int:
    """Extract memories from the turn starting at this user message. Returns how many were stored.

    Idempotent: a turn already extracted is skipped without an LLM call, so a job that runs
    twice (retry, reconciliation sweep) is harmless. LLM errors propagate and leave the turn
    unmarked, so it can be retried.
    """
    # 1. Read the turn. This session closes before the LLM call: no database connection
    #    is held for the seconds the model takes.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        question = await session.get(Message, user_message_id)
        if question is None or question.role != "user" or question.memories_extracted_at is not None:
            return 0
        answer = await session.scalar(
            select(Message).where(
                Message.conversation_id == question.conversation_id,
                Message.position == question.position + 1,
            )
        )

    # 2. Ask the model (slow, may fail), then embed what it found.
    memories = await extract_memories(
        llm, question.content, answer.content if answer else "", question.created_at.date()
    )
    vectors = (
        await run_in_threadpool(embedder.embed_passages, [m.content for m in memories])
        if memories
        else []
    )

    # 3. Save. Lock the message row and check again: another run of this job may have
    #    finished while we were waiting for the model. Then its result stands and ours is
    #    discarded, instead of storing every memory twice.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        locked = await session.get(Message, user_message_id, with_for_update=True)
        if locked is None or locked.memories_extracted_at is not None:
            return 0
        session.add_all(
            Memory(kind=m.kind, content=m.content, embedding=vector, source_message_id=locked.id)
            for m, vector in zip(memories, vectors, strict=True)
        )
        locked.memories_extracted_at = func.now()
        await session.commit()

    logger.info("Turn %s: stored %d memories", user_message_id, len(memories))
    return len(memories)
