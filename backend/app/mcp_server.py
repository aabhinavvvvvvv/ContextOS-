"""ContextOS as an MCP (Model Context Protocol) server.

MCP is a standard way for AI applications (Claude Desktop, IDE assistants, other agents)
to use external tools and data. This server exposes ContextOS's knowledge to them:

    tools      (the client's model decides to call them):
      search_knowledge(query)  hybrid search over documents, GitHub data and memories
      ask(question)            the full ContextOS agent: grounded answer with sources
      list_open_tasks()        every open task memory
    resources  (data the client application can read and attach as context):
      contextos://memories     all current memories

Run with the stdio transport (the client starts this process and talks over stdin/stdout):
    uv run python -m app.mcp_server
Read-only on purpose: actions that change things go through ContextOS's approval flow.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.concurrency import run_in_threadpool

from mcp.server.mcpserver import MCPServer

from app.answers.graph import answer_question
from app.config import get_settings
from app.db import create_db_engine
from app.embeddings import Embedder, get_embedder
from app.llm import LLM, get_llm
from app.memory.recall import RecalledMemory, is_current, related_memories
from app.memory.service import list_memories
from app.models import Memory
from app.search.service import hybrid_search


class Dependencies:
    """Created on first use (the embedding model takes a moment to load); tests replace them."""

    engine: AsyncEngine | None = None
    embedder: Embedder | None = None
    llm: LLM | None = None

    def get_engine(self) -> AsyncEngine:
        if self.engine is None:
            self.engine = create_db_engine(get_settings())
        return self.engine

    def get_embedder(self) -> Embedder:
        return self.embedder or get_embedder()

    def get_llm(self) -> LLM:
        return self.llm or get_llm()


deps = Dependencies()

server = MCPServer(
    name="ContextOS",
    instructions=(
        "ContextOS holds the user's documents, synced GitHub issues/PRs/commits and long-term "
        "memories (decisions, preferences, tasks). Use search_knowledge for raw evidence, ask for "
        "a grounded answer with citations, and list_open_tasks for the user's to-dos."
    ),
)


@server.tool()
async def search_knowledge(query: str, limit: int = 5) -> str:
    """Hybrid (semantic + keyword) search over the user's documents and GitHub data, plus the
    closest memories. Returns numbered excerpts with their source."""
    limit = max(1, min(limit, 10))
    vector = await run_in_threadpool(deps.get_embedder().embed_query, query)
    async with AsyncSession(deps.get_engine()) as session:
        hits = await hybrid_search(session, query, vector, limit=limit)
        memories = await related_memories(session, vector, limit=3)
    lines = [
        f"[{n}] {hit.filename}{' / ' + hit.heading if hit.heading else ''}\n{hit.text}"
        for n, hit in enumerate(hits, 1)
    ]
    lines += [f"[M{n}] ({m.kind}, {m.created_at:%Y-%m-%d}) {m.content}" for n, m in enumerate(memories, 1)]
    return "\n\n".join(lines) or "Nothing found."


@server.tool()
async def ask(question: str) -> str:
    """Ask ContextOS a question. Its agent searches documents, GitHub data and memories and
    answers only from them, citing sources as [n] and memories as [Mn]."""
    async with AsyncSession(deps.get_engine()) as session:
        answer = await answer_question(session, deps.get_embedder(), deps.get_llm(), question, top_k=5)
    sources = [f"[{c.number}] {c.hit.filename}" for c in answer.citations]
    sources += [f"[M{c.number}] {c.memory.content}" for c in answer.memory_citations]
    return answer.text + ("\n\nSources:\n" + "\n".join(sources) if sources else "")


@server.tool()
async def list_open_tasks() -> str:
    """The user's open tasks (task memories that are still current), oldest first."""
    async with AsyncSession(deps.get_engine()) as session:
        rows = await session.execute(
            select(Memory.id, Memory.kind, Memory.content, Memory.created_at)
            .where(Memory.kind == "task", is_current())
            .order_by(Memory.created_at)
        )
        tasks = [RecalledMemory(*row) for row in rows.all()]
    return "\n".join(f"- {t.content}" for t in tasks) or "No open tasks."


@server.resource("contextos://memories", mime_type="text/plain")
async def current_memories() -> str:
    """Everything ContextOS currently remembers about the user, newest first."""
    async with AsyncSession(deps.get_engine()) as session:
        rows = await list_memories(session, limit=200)
    return "\n".join(f"[{r.kind}] {r.content}" for r in rows) or "No memories yet."


if __name__ == "__main__":
    server.run("stdio")
