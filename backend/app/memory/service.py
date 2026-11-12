"""Long-term (semantic) memory: extract memories from a recorded turn and store them."""

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import Row, Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.concurrency import run_in_threadpool

from app.actions.service import audit
from app.embeddings import Embedder
from app.llm import LLM
from app.memory.extraction import extract_memories
from app.memory.recall import is_current, related_memories
from app.models import Memory, MemoryKind, Message

logger = logging.getLogger(__name__)

RELATED_MEMORIES = 5  # current memories shown to the extractor, so it can skip or replace them


async def unextracted_turn_ids(session: AsyncSession, *, limit: int) -> list[uuid.UUID]:
    """User messages whose memories haven't been extracted yet, oldest first, at most `limit`.

    Used by the worker's sweep. The limit keeps a backlog (e.g. turns recorded before
    memory existed) from firing dozens of LLM calls at once and using up the per-minute quota.
    """
    result = await session.scalars(
        select(Message.id)
        .where(Message.role == "user", Message.memories_extracted_at.is_(None))
        .order_by(Message.created_at, Message.position)
        .limit(limit)
    )
    return list(result.all())


def _memory_rows() -> Select:
    """Memories with the conversation each came from (the fields of MemoryRead)."""
    return select(
        Memory.id,
        Memory.kind,
        Memory.content,
        Memory.created_at,
        Memory.source_message_id,
        Message.conversation_id,
        Memory.superseded_by_id,
        Memory.superseded_at,
        Memory.expires_on,
    ).join(Message, Memory.source_message_id == Message.id)


async def list_memories(
    session: AsyncSession,
    *,
    limit: int,
    kind: MemoryKind | None = None,
    include_history: bool = False,
) -> Sequence[Row]:
    """Memories newest first. By default only current ones; with include_history, also
    those a newer memory replaced and those that expired."""
    query = _memory_rows().order_by(Memory.created_at.desc(), Memory.id).limit(limit)
    if kind is not None:
        query = query.where(Memory.kind == kind)
    if not include_history:
        query = query.where(is_current())
    return (await session.execute(query)).all()


async def get_memory(session: AsyncSession, memory_id: uuid.UUID) -> Row | None:
    return (await session.execute(_memory_rows().where(Memory.id == memory_id))).one_or_none()


async def edit_memory(
    session: AsyncSession, embedder: Embedder, memory_id: uuid.UUID, content: str
) -> Row | None:
    """Correct a memory's wording (None if it doesn't exist). The embedding is recomputed,
    so the memory is found by its new meaning."""
    [vector] = await run_in_threadpool(embedder.embed_passages, [content])
    result = await session.execute(
        update(Memory).where(Memory.id == memory_id).values(content=content, embedding=vector)
    )
    if result.rowcount == 0:
        return None
    audit(session, "user", "memory.edited", "memory", memory_id, {"content": content})
    await session.commit()
    return await get_memory(session, memory_id)


async def delete_memory(session: AsyncSession, memory_id: uuid.UUID) -> bool:
    """Forget a memory (False if it doesn't exist).

    A memory it had replaced becomes current again, so deleting a wrong update undoes it
    ("switched to Claude" deleted → "keep Gemini" is back).
    """
    await session.execute(
        update(Memory)
        .where(Memory.superseded_by_id == memory_id)
        .values(superseded_by_id=None, superseded_at=None)
    )
    result = await session.execute(delete(Memory).where(Memory.id == memory_id))
    if result.rowcount:
        audit(session, "user", "memory.deleted", "memory", memory_id)
    await session.commit()
    return result.rowcount > 0


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
        # The current memories closest to what the user said: the model sees them, so it
        # can skip what's already known and say which ones a new memory replaces.
        query_vector = await run_in_threadpool(embedder.embed_query, question.content)
        existing = await related_memories(session, query_vector, limit=RELATED_MEMORIES)

    # 2. Ask the model (slow, may fail), then embed what it found.
    memories = await extract_memories(
        llm,
        question.content,
        answer.content if answer else "",
        question.created_at.date(),
        existing=[row.content for row in existing],
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
        for m, vector in zip(memories, vectors, strict=True):
            memory = Memory(
                id=uuid.uuid4(),
                kind=m.kind,
                content=m.content,
                expires_on=m.expires_on,
                embedding=vector,
                source_message_id=locked.id,
            )
            session.add(memory)
            await session.flush()  # INSERT now, so the old memory's foreign key can point at it
            for number in m.replaces:  # e.g. [1] = the first existing memory shown to the model
                await session.execute(
                    update(Memory)
                    .where(Memory.id == existing[number - 1].id)
                    .values(superseded_by_id=memory.id, superseded_at=func.now())
                )
        locked.memories_extracted_at = func.now()
        await session.commit()

    logger.info("Turn %s: stored %d memories", user_message_id, len(memories))
    return len(memories)
