"""Background worker: runs queued jobs in its own process, separate from the API.

Start it from backend/ (Postgres and Redis must be running):
    uv run saq app.worker.worker_settings
"""

import logging
import uuid
from typing import Any

from saq import CronJob
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import create_db_engine
from app.embeddings import Embedder
from app.ingestion.service import ingest_document, unfinished_document_ids
from app.jobs import JobQueue, create_saq_queue
from app.llm import LLMNotConfiguredError, get_llm
from app.logging_config import configure_logging
from app.memory.service import remember_turn, unextracted_turn_ids

logger = logging.getLogger(__name__)

# Turns the sweep queues per minute. Live turns are queued by /ask as they happen; this only
# paces catch-up work, so a backlog can't use up the free tier's per-minute request limit.
MEMORY_SWEEP_BATCH = 5

# SAQ passes this dict to every hook and job. It starts as {"worker": <Worker>};
# startup() adds our shared resources to it.
Context = dict[str, Any]


async def startup(ctx: Context) -> None:
    settings = get_settings()
    # The SAQ CLI already configured logging at WARNING; replace it so INFO logs show.
    configure_logging(settings.log_level, replace_existing=True)
    ctx["settings"] = settings
    ctx["engine"] = create_db_engine(settings)
    # Loaded once per worker process and shared by all jobs (ONNX Runtime is thread-safe).
    ctx["embedder"] = Embedder(settings.model_cache_dir)
    try:
        ctx["llm"] = get_llm()
    except LLMNotConfiguredError:
        # Documents still get ingested without a key; memory extraction waits for one.
        logger.warning("No GEMINI_API_KEY: memory extraction is paused")
        ctx["llm"] = None
    await enqueue_unfinished_documents(ctx)
    await enqueue_unextracted_turns(ctx)


async def shutdown(ctx: Context) -> None:
    await ctx["engine"].dispose()


async def ingest_document_job(ctx: Context, *, document_id: str) -> None:
    await ingest_document(
        ctx["engine"], uuid.UUID(document_id), ctx["settings"].upload_dir, ctx["embedder"]
    )


async def enqueue_unfinished_documents(ctx: Context) -> None:
    """Re-queue every document the database says is unfinished.

    The database is the source of truth; the queue only delivers work. This recovers
    documents whose job never reached Redis (it was down during upload) or was lost.
    """
    async with AsyncSession(ctx["engine"]) as session:
        document_ids = await unfinished_document_ids(session)
    job_queue = JobQueue(ctx["worker"].queue)
    newly_queued = 0
    for document_id in document_ids:
        newly_queued += await job_queue.enqueue_ingestion(document_id)
    if newly_queued:
        logger.info("Re-queued %d unfinished document(s)", newly_queued)


async def remember_turn_job(ctx: Context, *, message_id: str) -> int:
    if ctx["llm"] is None:
        return 0  # no API key: the turn stays unextracted until one is configured
    return await remember_turn(ctx["engine"], ctx["llm"], ctx["embedder"], uuid.UUID(message_id))


async def enqueue_unextracted_turns(ctx: Context) -> None:
    """Queue memory extraction for turns that don't have it yet, a few per run.

    Catches turns whose job was lost or failed, and backfills turns recorded before
    memory extraction existed.
    """
    if ctx["llm"] is None:
        return
    async with AsyncSession(ctx["engine"]) as session:
        message_ids = await unextracted_turn_ids(session, limit=MEMORY_SWEEP_BATCH)
    job_queue = JobQueue(ctx["worker"].queue)
    newly_queued = 0
    for message_id in message_ids:
        newly_queued += await job_queue.enqueue_memory_extraction(message_id)
    if newly_queued:
        logger.info("Queued memory extraction for %d turn(s)", newly_queued)


worker_settings = {
    "queue": create_saq_queue(get_settings()),
    "functions": [ingest_document_job, remember_turn_job],
    # Every minute: safety nets for work whose job was lost or never enqueued.
    "cron_jobs": [
        CronJob(enqueue_unfinished_documents, cron="* * * * *"),
        CronJob(enqueue_unextracted_turns, cron="* * * * *"),
    ],
    "concurrency": 2,
    "startup": startup,
    "shutdown": shutdown,
}
