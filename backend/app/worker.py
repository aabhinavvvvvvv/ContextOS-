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
from app.ingestion.service import ingest_document, unfinished_document_ids
from app.jobs import JobQueue, create_saq_queue
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

# SAQ passes this dict to every hook and job. It starts as {"worker": <Worker>};
# startup() adds our shared resources to it.
Context = dict[str, Any]


async def startup(ctx: Context) -> None:
    settings = get_settings()
    # The SAQ CLI already configured logging at WARNING; replace it so INFO logs show.
    configure_logging(settings.log_level, replace_existing=True)
    ctx["settings"] = settings
    ctx["engine"] = create_db_engine(settings)
    await enqueue_unfinished_documents(ctx)


async def shutdown(ctx: Context) -> None:
    await ctx["engine"].dispose()


async def ingest_document_job(ctx: Context, *, document_id: str) -> None:
    await ingest_document(ctx["engine"], uuid.UUID(document_id), ctx["settings"].upload_dir)


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


worker_settings = {
    "queue": create_saq_queue(get_settings()),
    "functions": [ingest_document_job],
    # Every minute: a safety net for documents whose job was never enqueued.
    "cron_jobs": [CronJob(enqueue_unfinished_documents, cron="* * * * *")],
    "concurrency": 2,
    "startup": startup,
    "shutdown": shutdown,
}
