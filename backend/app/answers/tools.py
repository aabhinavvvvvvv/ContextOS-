"""The tools the answer agent can run. Each takes a step's question and returns evidence.

The planner (planning.py) only *names* a tool; this module decides what that name does.
A name that isn't registered here can never run.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.actions.service import latest_github_repo, propose
from app.config import Settings
from app.embeddings import Embedder
from app.memory.recall import RecalledMemory, is_current, related_memories
from app.models import Action, Memory
from app.search.service import SearchHit, hybrid_search

MEMORIES_PER_SEARCH = 3  # current memories closest to the query, given to the model
MAX_TASKS = 25  # list_tasks returns at most this many open tasks, oldest first


@dataclass(frozen=True)
class ToolContext:
    session: AsyncSession
    embedder: Embedder
    top_k: int  # document chunks per search
    settings: Settings  # the request's settings (e.g. ALLOWED_ACTIONS)


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


# The planner sometimes copies the request ("Open a GitHub issue for the login bug") instead
# of the payload ("Login bug"). Stripping the request phrasing in code is cheap and
# predictable; the user still sees (and approves) the final text.
_REQUEST_PREFIX = re.compile(
    r"^(?:please\s+)?(?:(?:open|create|file|raise|add)\s+(?:a\s+|an\s+|new\s+)*(?:github\s+)?"
    r"(?:issue|task|to-?do)\s*(?:for|about|on|to|:|-)?\s*(?:the\s+)?|remind\s+me\s+to\s+)",
    re.IGNORECASE,
)


def payload_text(question: str) -> str:
    """'Open a GitHub issue for the login timeout bug.' → 'Login timeout bug'."""
    text = _REQUEST_PREFIX.sub("", question.strip()).strip().rstrip(".").strip()
    return (text[:1].upper() + text[1:]) if text else question


async def propose_task(question: str, ctx: ToolContext) -> ToolResult:
    """Propose a task (the step question is the task). Nothing is saved until approved."""
    task = payload_text(question)
    action = await propose(ctx.session, ctx.settings, "create_task", {"content": task}, f"Add task: {task}")
    return ToolResult(proposals=[_proposal(action)])


async def propose_github_issue(question: str, ctx: ToolContext) -> ToolResult:
    """Propose opening an issue (the step question is its title) in the connected repo."""
    repo = await latest_github_repo(ctx.session)
    title = payload_text(question)
    action = await propose(
        ctx.session,
        ctx.settings,
        "create_github_issue",
        {"repo": repo, "title": title, "body": "Proposed by ContextOS and approved by the user."},
        f"Open a GitHub issue in {repo}: {title}",
    )
    return ToolResult(proposals=[_proposal(action)])


def _proposal(action: Action) -> dict:
    return {"id": str(action.id), "kind": action.kind, "summary": action.summary}


Tool = Callable[[str, ToolContext], Awaitable[ToolResult]]

TOOLS: dict[str, Tool] = {
    "search": search,
    "list_tasks": list_tasks,
    "propose_task": propose_task,
    "propose_github_issue": propose_github_issue,
}
