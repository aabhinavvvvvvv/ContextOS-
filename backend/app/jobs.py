"""Background job queue (SAQ on Redis).

Only this module and app/worker.py import SAQ, so replacing the queue library later
means changing these two files. Everything else uses JobQueue.
"""

import uuid

from fastapi import Request
from saq import Queue

from app.config import Settings

INGEST_DOCUMENT_JOB = "ingest_document_job"  # must match the function name in app/worker.py


def create_saq_queue(settings: Settings) -> Queue:
    return Queue.from_url(settings.redis_url, name=settings.queue_name)


class JobQueue:
    """The job operations ContextOS needs, and nothing more."""

    def __init__(self, queue: Queue) -> None:
        self.queue = queue

    async def enqueue_ingestion(self, document_id: uuid.UUID) -> bool:
        """Queue ingestion for a document. Returns False if a job for it is already queued."""
        job = await self.queue.enqueue(
            INGEST_DOCUMENT_JOB,
            document_id=str(document_id),
            # The same key twice is a no-op, so a document never has two jobs at once.
            key=f"ingest:{document_id}",
            timeout=300,  # seconds; SAQ's default of 10 is too short for large PDFs
            retries=3,  # total attempts, for transient failures such as a database restart
            retry_delay=5.0,
            retry_backoff=True,  # wait longer after each failed attempt
        )
        return job is not None


def get_job_queue(request: Request) -> JobQueue:
    """FastAPI dependency: the queue connected at startup (see lifespan in main.py)."""
    return request.app.state.job_queue
