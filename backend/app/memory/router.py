"""HTTP routes for long-term memory: the worker writes memories, the user can see,
correct and delete them here."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db import SessionDep
from app.embeddings import Embedder, get_embedder
from app.memory import service
from app.memory.schemas import MemoryEdit, MemoryRead
from app.models import MemoryKind

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("")
async def list_memories(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    kind: MemoryKind | None = None,
    include_history: bool = False,
) -> list[MemoryRead]:
    """What ContextOS has learned from your conversations, newest first.

    Only current memories by default; `include_history=true` also shows the ones a newer
    memory replaced (with `superseded_by_id`) and the ones that expired (`expires_on` in
    the past). Extraction runs in the background worker, so a new turn's memories appear
    a few seconds after its answer.
    """
    rows = await service.list_memories(
        session, limit=limit, kind=kind, include_history=include_history
    )
    return [MemoryRead.model_validate(row) for row in rows]


@router.patch("/{memory_id}")
async def edit_memory(
    memory_id: uuid.UUID,
    edit: MemoryEdit,
    session: SessionDep,
    embedder: Annotated[Embedder, Depends(get_embedder)],
) -> MemoryRead:
    """Correct a memory's wording. It is re-embedded, so recall finds it by its new meaning."""
    row = await service.edit_memory(session, embedder, memory_id, " ".join(edit.content.split()))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
    return MemoryRead.model_validate(row)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: uuid.UUID, session: SessionDep) -> None:
    """Forget a memory. If it had replaced an older one, the older one is current again."""
    if not await service.delete_memory(session, memory_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
