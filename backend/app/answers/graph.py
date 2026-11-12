"""The answer workflow as a LangGraph graph.

    START → rewrite → retrieve ─┬─ found something ─→ generate ──→ END
                                └─ found nothing ───→ not_found ─→ END

Phase 4.1: the same steps answer_question used to run as straight-line code, now as
nodes and edges. Later phases add planning, tools and a loop as new nodes and edges.
The building blocks (prompts, citation checks) stay in service.py.
"""

import operator
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.answers.service import (
    MEMORIES_IN_ANSWER,
    NOT_FOUND_ANSWER,
    SYSTEM_PROMPT,
    Answer,
    History,
    build_prompt,
    cited_memories,
    cited_sources,
    standalone_question,
)
from app.embeddings import Embedder
from app.llm import LLM
from app.memory.recall import RecalledMemory, related_memories
from app.search.service import SearchHit, hybrid_search


@dataclass(frozen=True)
class AnswerContext:
    """Per-request tools the nodes use. Kept out of the state: they aren't facts about the
    question, and a database session couldn't be saved if we ever checkpoint the state."""

    session: AsyncSession
    embedder: Embedder
    llm: LLM


class AnswerState(TypedDict, total=False):  # total=False: keys appear as nodes fill them
    # Input
    question: str
    history: History
    top_k: int
    # Written by the nodes
    search_question: str  # the question actually searched and answered (standalone)
    hits: list[SearchHit]
    memories: list[RecalledMemory]
    text: str
    # Reducer: each LLM node returns only its own usage, and LangGraph adds it to the total
    # (without operator.add, the second call's numbers would overwrite the first's).
    input_tokens: Annotated[int, operator.add]
    output_tokens: Annotated[int, operator.add]


# --- Nodes: state in, the keys they change out -----------------------------------------


async def rewrite(state: AnswerState, runtime: Runtime[AnswerContext]) -> dict:
    """Make a follow-up standalone ("Why?" → "Why did we …?"). No LLM call without history."""
    result = await standalone_question(runtime.context.llm, state["question"], state["history"])
    return {
        "search_question": result.question,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


async def retrieve(state: AnswerState, runtime: Runtime[AnswerContext]) -> dict:
    """Hybrid search over document chunks plus the closest current memories (one embedding)."""
    ctx = runtime.context
    query = state["search_question"]
    vector = await run_in_threadpool(ctx.embedder.embed_query, query)
    hits = await hybrid_search(ctx.session, query, vector, limit=state["top_k"])
    memories = await related_memories(ctx.session, vector, limit=MEMORIES_IN_ANSWER)
    return {"hits": hits, "memories": memories}


def found_anything(state: AnswerState) -> Literal["generate", "not_found"]:
    """Conditional edge: only call the model when there is something to ground an answer in."""
    return "generate" if state["hits"] or state["memories"] else "not_found"


async def generate(state: AnswerState, runtime: Runtime[AnswerContext]) -> dict:
    prompt = build_prompt(state["search_question"], state["hits"], state["memories"])
    result = await runtime.context.llm.generate(system=SYSTEM_PROMPT, prompt=prompt)
    return {
        "text": result.text,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


def not_found(state: AnswerState) -> dict:
    return {"text": NOT_FOUND_ANSWER}


# --- Wiring ---------------------------------------------------------------------------


def build_answer_graph():  # returns a CompiledStateGraph
    graph = StateGraph(AnswerState, context_schema=AnswerContext)
    graph.add_node("rewrite", rewrite)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("not_found", not_found)
    graph.add_edge(START, "rewrite")
    graph.add_edge("rewrite", "retrieve")
    # The router's Literal return type tells LangGraph the possible next nodes.
    graph.add_conditional_edges("retrieve", found_anything)
    graph.add_edge("generate", END)
    graph.add_edge("not_found", END)
    return graph.compile()  # checks the wiring (e.g. an edge to a missing node) once, at import


answer_graph = build_answer_graph()


async def answer_question(
    session: AsyncSession,
    embedder: Embedder,
    llm: LLM,
    question: str,
    *,
    top_k: int,
    history: Sequence[tuple[str, str]] = (),
) -> Answer:
    """Answer from the documents and memories. With history, a follow-up is first made standalone."""
    state = await answer_graph.ainvoke(
        {
            "question": question,
            "history": list(history),
            "top_k": top_k,
            "input_tokens": 0,
            "output_tokens": 0,
        },
        context=AnswerContext(session=session, embedder=embedder, llm=llm),
    )
    return Answer(
        text=state["text"],
        citations=cited_sources(state["text"], state["hits"]),
        sources_considered=len(state["hits"]),
        input_tokens=state["input_tokens"],
        output_tokens=state["output_tokens"],
        standalone_question=state["search_question"] if history else None,
        memory_citations=cited_memories(state["text"], state["memories"]),
    )
