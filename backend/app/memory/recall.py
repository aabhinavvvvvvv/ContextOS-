"""Reading memories back: the current memories closest in meaning to a question.

Separate from service.py so the answer pipeline can use it without an import cycle
(service.py → extraction.py → answers/service.py → here).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory


def is_current() -> ColumnElement[bool]:
    """SQL condition for "still true": not replaced by a newer memory and not expired.

    current_date is Postgres's date (UTC in our container); a memory is still true on the
    day it expires.
    """
    return Memory.superseded_by_id.is_(None) & or_(
        Memory.expires_on.is_(None), Memory.expires_on >= func.current_date()
    )


@dataclass(frozen=True)
class RecalledMemory:
    id: uuid.UUID
    kind: str
    content: str
    created_at: datetime  # when the user said it


async def related_memories(
    session: AsyncSession, query_vector: list[float], *, limit: int
) -> list[RecalledMemory]:
    """The current memories closest in meaning to the query vector."""
    result = await session.execute(
        select(Memory.id, Memory.kind, Memory.content, Memory.created_at)
        .where(is_current())
        .order_by(Memory.embedding.cosine_distance(query_vector))
        .limit(limit)
    )
    return [RecalledMemory(*row) for row in result.all()]
