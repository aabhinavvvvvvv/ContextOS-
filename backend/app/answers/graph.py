"""The answer agent as a LangGraph graph.

    START → rewrite → plan → act ⇄ check ─┬─ evidence found ─→ generate ──→ END
                                          └─ nothing found ──→ not_found ─→ END

rewrite: a follow-up becomes standalone (LLM call only with history).
plan:    simple questions → one search (no LLM call); others → LLM picks tool steps.
act:     runs the pending steps (tools.py); evidence is appended to the state.
check:   planned questions only: LLM says whether evidence is missing → back to act once.
generate: answers from the numbered evidence with verified citations.

At most 4 LLM calls per question (rewrite, plan, check, generate). Failures degrade: if
rewrite/plan/check fail, the agent continues on the simple path; a failing tool is
skipped. Only a failed generate call reaches the user as an error.
"""

import logging
import operator
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession

from app.answers.planning import (
    PlanStep,
    check_evidence,
    needs_planning,
    plan_steps,
    single_search,
)
from app.answers.service import (
    NOT_FOUND_ANSWER,
    SYSTEM_PROMPT,
    Answer,
    History,
    build_prompt,
    cited_memories,
    cited_sources,
    question_with_parts,
    standalone_question,
)
from app.answers.tools import TOOLS, ToolContext
from app.config import Settings, get_settings
from app.embeddings import Embedder
from app.llm import LLM, LLMError
from app.memory.recall import RecalledMemory
from app.search.service import SearchHit

logger = logging.getLogger(__name__)

MAX_ROUNDS = 2  # rounds of tool calls: the plan, plus at most one round the check adds
RECURSION_LIMIT = 15  # LangGraph's own safety net against an endless loop (node visits)


@dataclass(frozen=True)
class AnswerContext:
    """Per-request tools the nodes use. Kept out of the state: they aren't facts about the
    question, and a database session couldn't be saved if we ever checkpoint the state."""

    session: AsyncSession
    embedder: Embedder
    llm: LLM
    settings: Settings


class AnswerState(TypedDict, total=False):  # total=False: keys appear as nodes fill them
    # Input
    question: str
    history: History
    top_k: int
    # Written by the nodes
    search_question: str  # the question actually answered (standalone)
    parts: list[str]  # the questions of the planned steps (listed in the answer prompt)
    planned: bool  # True when the LLM planned (then the evidence check runs)
    pending: list[PlanStep]  # steps act should run next (replaced each time)
    rounds: int
    text: str
    # Reducers: these lists/numbers grow as nodes add to them.
    tried: Annotated[list[dict], operator.add]  # {"tool", "question", "results"}
    hits: Annotated[list[SearchHit], operator.add]
    memories: Annotated[list[RecalledMemory], operator.add]
    proposals: Annotated[list[dict], operator.add]
    timeline: Annotated[list[dict], operator.add]
    input_tokens: Annotated[int, operator.add]
    output_tokens: Annotated[int, operator.add]


def event(step: str, started: float, summary: str, error: str | None = None) -> dict:
    """One timeline entry: what a node did and how long it took."""
    return {
        "step": step,
        "summary": summary,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "error": error,
    }


def unique_evidence(state: AnswerState) -> tuple[list[SearchHit], list[RecalledMemory]]:
    """Hits and memories without duplicates (several steps may find the same chunk), in the
    order found. Used for the prompt AND for checking citations, so the numbers match."""
    hits: dict[uuid.UUID, SearchHit] = {}
    for hit in state.get("hits", []):
        hits.setdefault(hit.chunk_id, hit)  # first (best-ranked) occurrence wins
    memories: dict[uuid.UUID, RecalledMemory] = {}
    for memory in state.get("memories", []):
        memories.setdefault(memory.id, memory)
    return list(hits.values()), list(memories.values())


# --- Nodes: state in, the keys they change out -----------------------------------------


async def rewrite(state: AnswerState, runtime: Runtime[AnswerContext]) -> dict:
    """Make a follow-up standalone ("Why?" → "Why did we …?"). No LLM call without history."""
    started = time.perf_counter()
    if not state["history"]:
        return {"search_question": state["question"]}
    try:
        result = await standalone_question(runtime.context.llm, state["question"], state["history"])
    except LLMError as exc:
        # Degrade: searching the question as typed is worse, but still an answer.
        return {
            "search_question": state["question"],
            "timeline": [event("rewrite", started, "Could not rewrite; using the question as typed", str(exc))],
        }
    return {
        "search_question": result.question,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "timeline": [event("rewrite", started, f"Follow-up rewritten: {result.question}")],
    }


async def plan(state: AnswerState, runtime: Runtime[AnswerContext]) -> dict:
    """Simple question → one search, no LLM call. Otherwise the LLM chooses tool steps."""
    started = time.perf_counter()
    question = state["search_question"]
    if not needs_planning(question):
        steps = single_search(question)
        return {
            "pending": steps,
            "parts": [question],
            "planned": False,
            "timeline": [event("plan", started, "Simple question: one search")],
        }
    try:
        steps, input_tokens, output_tokens = await plan_steps(runtime.context.llm, question)
    except LLMError as exc:
        return {
            "pending": single_search(question),
            "parts": [question],
            "planned": False,
            "timeline": [event("plan", started, "Planning failed; falling back to one search", str(exc))],
        }
    summary = "; ".join(f"{s.tool}({s.question})" for s in steps)
    return {
        "pending": steps,
        "parts": list(dict.fromkeys(s.question for s in steps)),
        "planned": True,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "timeline": [event("plan", started, f"Planned {len(steps)} step(s): {summary}")],
    }


async def act(state: AnswerState, runtime: Runtime[AnswerContext]) -> dict:
    """Run every pending step. A failing tool is recorded and skipped, not fatal."""
    ctx = runtime.context
    tool_ctx = ToolContext(ctx.session, ctx.embedder, state["top_k"], ctx.settings)
    update: dict = {"hits": [], "memories": [], "proposals": [], "tried": [], "timeline": []}
    for step in state["pending"]:
        started = time.perf_counter()
        try:
            # SAVEPOINT: a failing tool undoes only its own changes. A full rollback() would
            # also discard what earlier tools did in this request (e.g. a proposed action).
            async with ctx.session.begin_nested():
                result = await TOOLS[step.tool](step.question, tool_ctx)
        except Exception as exc:  # one broken tool shouldn't sink the whole answer
            logger.exception("Tool %s failed", step.tool)
            update["tried"].append({"tool": step.tool, "question": step.question, "results": 0})
            update["timeline"].append(event(f"tool:{step.tool}", started, step.question, f"{type(exc).__name__}: {exc}"))
            continue
        update["hits"] += result.hits
        update["memories"] += result.memories
        update["proposals"] += result.proposals
        update["tried"].append({"tool": step.tool, "question": step.question, "results": result.count})
        update["timeline"].append(event(f"tool:{step.tool}", started, f"{step.question} → {result.count} result(s)"))
    update["pending"] = []
    update["rounds"] = state.get("rounds", 0) + 1
    return update


async def check(state: AnswerState, runtime: Runtime[AnswerContext]) -> dict:
    """Planned questions only: is evidence missing? If so, queue new steps (one more round)."""
    if not state["planned"] or state["rounds"] >= MAX_ROUNDS:
        return {}
    started = time.perf_counter()
    hits, memories = unique_evidence(state)
    question = question_with_parts(state["search_question"], state["parts"])
    try:
        steps, input_tokens, output_tokens = await check_evidence(
            runtime.context.llm, question, state["tried"], hits, memories
        )
    except LLMError as exc:
        return {"timeline": [event("check", started, "Check failed; answering with what was found", str(exc))]}
    summary = (
        "Evidence covers the question"
        if not steps
        else "Looking again: " + "; ".join(f"{s.tool}({s.question})" for s in steps)
    )
    return {
        "pending": steps,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "timeline": [event("check", started, summary)],
    }


def next_step(state: AnswerState) -> Literal["act", "generate", "not_found"]:
    """Conditional edge after check: loop, answer, or admit there is nothing."""
    if state.get("pending"):
        return "act"
    if state.get("hits") or state.get("memories") or state.get("proposals"):
        return "generate"
    return "not_found"


async def generate(state: AnswerState, runtime: Runtime[AnswerContext]) -> dict:
    started = time.perf_counter()
    hits, memories = unique_evidence(state)
    question = question_with_parts(state["search_question"], state["parts"])
    prompt = build_prompt(question, hits, memories, state.get("proposals", []))
    result = await runtime.context.llm.generate(system=SYSTEM_PROMPT, prompt=prompt)
    return {
        "text": result.text,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "timeline": [event("generate", started, f"Answered from {len(hits)} source(s) and {len(memories)} memory(ies)")],
    }


def not_found(state: AnswerState) -> dict:
    return {
        "text": NOT_FOUND_ANSWER,
        "timeline": [event("generate", time.perf_counter(), "Nothing found: no model call")],
    }


# --- Wiring ---------------------------------------------------------------------------


def build_answer_graph():  # returns a CompiledStateGraph
    graph = StateGraph(AnswerState, context_schema=AnswerContext)
    graph.add_node("rewrite", rewrite)
    graph.add_node("plan", plan)
    graph.add_node("act", act)
    graph.add_node("check", check)
    graph.add_node("generate", generate)
    graph.add_node("not_found", not_found)
    graph.add_edge(START, "rewrite")
    graph.add_edge("rewrite", "plan")
    graph.add_edge("plan", "act")
    graph.add_edge("act", "check")
    graph.add_conditional_edges("check", next_step)  # the loop: check → act → check …
    graph.add_edge("generate", END)
    graph.add_edge("not_found", END)
    return graph.compile()


answer_graph = build_answer_graph()


async def answer_question(
    session: AsyncSession,
    embedder: Embedder,
    llm: LLM,
    question: str,
    *,
    top_k: int,
    history: Sequence[tuple[str, str]] = (),
    settings: Settings | None = None,
) -> Answer:
    """Answer from the documents and memories, planning tool steps for complex questions."""
    state = await answer_graph.ainvoke(
        {
            "question": question,
            "history": list(history),
            "top_k": top_k,
            "rounds": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        },
        config={"recursion_limit": RECURSION_LIMIT},
        context=AnswerContext(session=session, embedder=embedder, llm=llm, settings=settings or get_settings()),
    )
    hits, memories = unique_evidence(state)
    parts = state["parts"]
    return Answer(
        text=state["text"],
        citations=cited_sources(state["text"], hits),
        sources_considered=len(hits),
        input_tokens=state["input_tokens"],
        output_tokens=state["output_tokens"],
        standalone_question=state["search_question"] if history else None,
        memory_citations=cited_memories(state["text"], memories),
        sub_questions=parts if len(parts) > 1 else [],
        timeline=state.get("timeline", []),
        proposals=state.get("proposals", []),
    )

