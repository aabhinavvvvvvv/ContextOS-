"""HTTP routes for long-term memory (written by the background worker, read here)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.db import SessionDep
from app.memory import service
from app.memory.schemas import MemoryRead
from app.models import MemoryKind

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("")
async def list_memories(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    kind: MemoryKind | None = None,
    include_superseded: bool = False,
) -> list[MemoryRead]:
    """What ContextOS has learned from your conversations, newest first.

    Only current memories by default; `include_superseded=true` also shows the ones a newer
    memory replaced (with `superseded_by_id`). Extraction runs in the background worker,
    so a new turn's memories appear a few seconds after its answer.
    """
    rows = await service.list_memories(
        session, limit=limit, kind=kind, include_superseded=include_superseded
    )
    return [MemoryRead.model_validate(row) for row in rows]
