"""ContextOS benchmark: 60 scenarios over a fictional project ("Atlas").

    uv run python -m evals.run            offline: retrieval + memory recall (free, ~1 min)
    uv run python -m evals.run --live     also planning/tool choice + end-to-end answers
                                          (Gemini free tier, throttled: ~6 minutes)

Uses its own database (contextos_eval), rebuilt on every run from evals/corpus and
evals/memories.json through the real ingestion pipeline and embedding model. Writes a JSON
report to evals/results/ and prints a summary.
"""

import argparse
import asyncio
import io
import json
import os
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

# Before any app import: point the settings at the eval database and a scratch upload dir.
os.environ["POSTGRES_DB"] = "contextos_eval"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="contextos-eval-")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine  # noqa: E402

from app.answers.graph import answer_question  # noqa: E402
from app.answers.planning import ACTION_TOOLS, plan_steps  # noqa: E402
from app.answers.service import NOT_FOUND_ANSWER  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import build_database_url, create_db_engine  # noqa: E402
from app.documents.service import create_document  # noqa: E402
from app.embeddings import Embedder  # noqa: E402
from app.ingestion.service import ingest_document  # noqa: E402
from app.llm import LLMRateLimitError, get_llm  # noqa: E402
from app.memory.recall import related_memories  # noqa: E402
from app.models import Conversation, Memory, Message  # noqa: E402
from app.search.service import hybrid_search  # noqa: E402

EVALS = Path(__file__).resolve().parent
BACKEND = EVALS.parent
TOP_K = 5


def load_scenarios() -> list[dict]:
    return [json.loads(line) for line in (EVALS / "scenarios.jsonl").read_text().splitlines() if line.strip()]


# --- Setup: a fresh eval database ---------------------------------------------------------


async def create_database() -> None:
    admin = create_async_engine(build_database_url(get_settings()).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            if not await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = 'contextos_eval'")):
                await conn.execute(text('CREATE DATABASE "contextos_eval"'))
    finally:
        await admin.dispose()


async def load_data(engine: AsyncEngine, embedder: Embedder) -> None:
    settings = get_settings()
    async with engine.begin() as conn:  # every table, so each run starts from the same state
        await conn.execute(text(
            "TRUNCATE llm_calls, request_logs, audit_log, actions, connectors, documents, chunks, "
            "conversations, messages, memories"
        ))
    for path in sorted((EVALS / "corpus").iterdir()):
        # expire_on_commit=False: create_document commits, and reading document.id from an
        # expired object would be hidden I/O (MissingGreenlet), the same lesson as Phase 3.
        async with AsyncSession(engine, expire_on_commit=False) as session:
            document = await create_document(
                session, filename=path.name, source=io.BytesIO(path.read_bytes()),
                upload_dir=settings.upload_dir, max_bytes=settings.max_upload_bytes,
            )
            document_id = document.id
        await ingest_document(engine, document_id, settings.upload_dir, embedder)
    seeds = json.loads((EVALS / "memories.json").read_text())
    vectors = embedder.embed_passages([m["content"] for m in seeds])
    async with AsyncSession(engine) as session:
        conversation = Conversation(title="Seed memories")
        session.add(conversation)
        await session.flush()
        message = Message(conversation_id=conversation.id, position=0, role="user", content="(seed)")
        session.add(message)
        await session.flush()
        session.add_all(
            Memory(kind=m["kind"], content=m["content"], embedding=v, source_message_id=message.id)
            for m, v in zip(seeds, vectors, strict=True)
        )
        await session.commit()


# --- Offline evals --------------------------------------------------------------------------


async def eval_retrieval(engine: AsyncEngine, embedder: Embedder, s: dict) -> dict:
    vector = embedder.embed_query(s["question"])
    async with AsyncSession(engine) as session:
        hits = await hybrid_search(session, s["question"], vector, limit=TOP_K)
    rank = next(
        (i for i, h in enumerate(hits, 1) if h.filename == s["doc"] and s["expect"].lower() in h.text.lower()), None
    )
    return {"hit@1": rank == 1, "hit@5": rank is not None, "rr": 1 / rank if rank else 0.0,
            "top": [h.filename for h in hits[:3]]}


async def eval_memory(engine: AsyncEngine, embedder: Embedder, s: dict) -> dict:
    vector = embedder.embed_query(s["question"])
    async with AsyncSession(engine) as session:
        memories = await related_memories(session, vector, limit=3)
    return {"hit@3": any(s["expect"].lower() in m.content.lower() for m in memories),
            "top": [m.content[:60] for m in memories]}


# --- Live evals -----------------------------------------------------------------------------


async def with_retry(make_call):
    """One retry after a rate limit: the free tier counts requests per minute."""
    try:
        return await make_call()
    except LLMRateLimitError:
        await asyncio.sleep(65)
        return await make_call()


async def eval_planning(llm, s: dict) -> dict:
    started = time.perf_counter()
    steps, tokens_in, tokens_out = await with_retry(lambda: plan_steps(llm, s["question"]))
    tools = {step.tool for step in steps}
    required = set(s["tools"])
    ok = required <= tools and len(steps) <= s["max_steps"] and (tools & ACTION_TOOLS) <= required
    return {"ok": ok, "steps": [f"{st.tool}: {st.question}" for st in steps],
            "latency_ms": round((time.perf_counter() - started) * 1000), "tokens": tokens_in + tokens_out}


async def eval_answer(engine: AsyncEngine, embedder: Embedder, llm, s: dict) -> dict:
    started = time.perf_counter()

    async def ask():
        async with AsyncSession(engine) as session:
            return await answer_question(session, embedder, llm, s["question"], top_k=TOP_K)

    answer = await with_retry(ask)
    reply = answer.text.casefold()
    refused = answer.text.strip() == NOT_FOUND_ANSWER
    keywords = s.get("expect_all", []) + s.get("expect_any", [])
    if s.get("refusal"):
        correct, cited, supported = refused, True, True
    else:
        correct = (
            not refused
            and all(k.casefold() in reply for k in s.get("expect_all", []))
            and (not s.get("expect_any") or any(k.casefold() in reply for k in s["expect_any"]))
        )
        if s.get("cite") == "memory":
            cited = bool(answer.memory_citations)
            evidence = [c.memory.content for c in answer.memory_citations]
        else:
            cited = bool(answer.citations)
            evidence = [c.hit.text for c in answer.citations]
        # Citation support: does a cited source actually contain the expected fact?
        supported = any(k.casefold() in e.casefold() for e in evidence for k in keywords)
    return {"correct": correct, "cited": cited, "supported": supported, "refused": refused,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "tokens": answer.input_tokens + answer.output_tokens,
            "steps": [e["step"] for e in answer.timeline], "answer": answer.text[:300]}


# --- Report -----------------------------------------------------------------------------------


def rate(rows: list[dict], key: str) -> str:
    return f"{sum(r[key] for r in rows)}/{len(rows)} ({100 * sum(r[key] for r in rows) / len(rows):.0f}%)" if rows else "-"


def summarise(results: dict[str, list[dict]]) -> list[str]:
    lines = []
    if rows := results.get("retrieval"):
        mrr = statistics.mean(r["rr"] for r in rows)
        lines.append(f"retrieval   hit@1 {rate(rows, 'hit@1')}   hit@5 {rate(rows, 'hit@5')}   MRR {mrr:.3f}")
    if rows := results.get("memory"):
        lines.append(f"memory      hit@3 {rate(rows, 'hit@3')}")
    if rows := results.get("planning"):
        lines.append(f"planning    correct tools {rate(rows, 'ok')}")
    if rows := results.get("answer"):
        refusal_rows = [r for r in rows if r["expects_refusal"]]
        answer_rows = [r for r in rows if not r["expects_refusal"]]
        latencies = sorted(r["latency_ms"] for r in rows)
        lines.append(f"answers     correct {rate(answer_rows, 'correct')}   refusals {rate(refusal_rows, 'correct')}")
        lines.append(f"citations   present {rate(answer_rows, 'cited')}   supported {rate(answer_rows, 'supported')}")
        lines.append(
            f"latency     p50 {statistics.median(latencies) / 1000:.1f} s   "
            f"p95 {latencies[min(len(latencies) - 1, round(0.95 * len(latencies)))] / 1000:.1f} s   "
            f"tokens/answer {statistics.mean(r['tokens'] for r in rows):.0f}"
        )
    return lines


async def main(live: bool, pause: float) -> None:
    settings = get_settings()
    embedder = Embedder(settings.model_cache_dir)
    engine = create_db_engine(settings)
    results: dict[str, list[dict]] = {}
    try:
        print("Loading the Atlas corpus and memories into contextos_eval ...")
        await load_data(engine, embedder)
        scenarios = load_scenarios()
        for s in scenarios:
            category = s["category"]
            if category == "retrieval":
                row = await eval_retrieval(engine, embedder, s)
            elif category == "memory":
                row = await eval_memory(engine, embedder, s)
            elif not live:
                continue
            elif category == "planning":
                row = await eval_planning(get_llm(), s)
                await asyncio.sleep(pause)
            else:
                row = await eval_answer(engine, embedder, get_llm(), s)
                row["expects_refusal"] = bool(s.get("refusal"))
                await asyncio.sleep(pause)
            results.setdefault(category, []).append({"id": s["id"], "question": s["question"], **row})
            if category in ("planning", "answer"):
                print(f"  {s['id']} done")
    finally:
        await engine.dispose()

    summary = summarise(results)
    out = EVALS / "results"
    out.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2, default=str))
    print("\n" + "\n".join(summary))
    failed = [
        (cat, r["id"], r["question"])
        for cat, rows in results.items()
        for r in rows
        if not (r.get("hit@5", r.get("hit@3", r.get("ok", r.get("correct", True)))))
    ]
    if failed:
        print("\nMisses:")
        for cat, sid, question in failed:
            print(f"  [{cat}] {sid}: {question}")
    print(f"\nFull report: evals/results/{stamp}.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="also run the LLM scenarios (uses Gemini quota)")
    parser.add_argument("--pause", type=float, default=8.0, help="seconds between live scenarios (rate limit)")
    args = parser.parse_args()
    asyncio.run(create_database())
    # Outside any event loop: Alembic's env.py calls asyncio.run() itself, and an event
    # loop can't be started from inside a running one.
    command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    asyncio.run(main(args.live, args.pause))
