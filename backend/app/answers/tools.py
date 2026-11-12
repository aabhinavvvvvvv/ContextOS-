"""The tools the answer agent can run. Each takes a step's question and returns evidence.

The planner (planning.py) only *names* a tool; this module decides what that name does.
A name that isn't registered here can never run.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.embeddings import Embedder
from app.memory.recall import RecalledMemory, is_current, related_memories
from app.models import Memory
from app.search.service import SearchHit, hybrid_search

MEMORIES_PER_SEARCH = 3  # current memories closest to the query, given to the model
MAX_TASKS = 25  # list_tasks returns at most this many open tasks, oldest first


@dataclass(frozen=True)
class ToolContext:
    session: AsyncSession
    embedder: Embedder
    top_k: int  # document chunks per search
    extra: dict = field(default_factory=dict)  # per-request data (e.g. the conversation)


@dataclass
class ToolResult:
    hits: list[SearchHit] = field(default_factory=list)
    memories: list[RecalledMemory] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)  # Phase 6: actions awaiting approval

    @property
    def count(self) -> int:
        return len(self.hits) + len(self.memories) + len(self.proposals)


async def search(question: str, ctx: ToolContext) -> ToolResult:
    """Hybrid search over document chunks plus the closest current memories (one embedding)."""
    vector = await run_in_threadpool(ctx.embedder.embed_query, question)
    hits = await hybrid_search(ctx.session, question, vector, limit=ctx.top_k)
    memories = await related_memories(ctx.session, vector, limit=MEMORIES_PER_SEARCH)
    return ToolResult(hits=hits, memories=memories)


async def list_tasks(question: str, ctx: ToolContext) -> ToolResult:
    """Every current task memory: "what is unfinished?" needs all of them, not the 3 most similar."""
    rows = await ctx.session.execute(
        select(Memory.id, Memory.kind, Memory.content, Memory.created_at)
        .where(Memory.kind == "task", is_current())
        .order_by(Memory.created_at)
        .limit(MAX_TASKS)
    )
    return ToolResult(memories=[RecalledMemory(*row) for row in rows.all()])


Tool = Callable[[str, ToolContext], Awaitable[ToolResult]]

TOOLS: dict[str, Tool] = {
    "search": search,
    "list_tasks": list_tasks,
}
