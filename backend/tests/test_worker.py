"""The real queue and worker, against Redis. Run with Postgres and Redis up."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from saq import Worker
from sqlalchemy import text

from app.config import get_settings
from app.db import create_db_engine
from app.jobs import JobQueue, create_saq_queue, get_job_queue
from app.main import app
from app.worker import shutdown, startup, worker_settings

# The real worker's startup loads the real embedding model.
pytestmark = [pytest.mark.integration, pytest.mark.model]


class DroppingJobQueue:
    """Simulates a lost job: the upload succeeds, but nothing reaches the queue."""

    async def enqueue_ingestion(self, document_id: uuid.UUID) -> bool:
        return False


class BrokenJobQueue:
    """Simulates Redis being down during upload."""

    async def enqueue_ingestion(self, document_id: uuid.UUID) -> bool:
        raise ConnectionError("Redis is down")


def upload_without_ingesting(client: TestClient, content: bytes) -> str:
    app.dependency_overrides[get_job_queue] = lambda: DroppingJobQueue()
    response = client.post("/documents", files={"file": ("notes.md", content)})
    assert response.status_code == 201
    return response.json()["id"]


def run_worker_until_idle() -> None:
    """Start a real SAQ worker in burst mode: it processes everything queued, then exits."""

    async def run() -> None:
        queue = create_saq_queue(get_settings())
        await queue.connect()
        worker = Worker(
            queue,
            functions=worker_settings["functions"],
            startup=startup,
            shutdown=shutdown,
            burst=True,
            dequeue_timeout=1,  # burst mode: treat 1s with no new job as "queue is empty"
        )
        try:
            await worker.start()
        finally:
            await queue.disconnect()

    asyncio.run(run())


def set_status(document_id: str, status: str) -> None:
    async def run() -> None:
        engine = create_db_engine(get_settings())
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text("UPDATE documents SET status = :status WHERE id = :id"),
                    {"status": status, "id": document_id},
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_worker_recovers_and_ingests_a_document_whose_job_was_lost(client: TestClient) -> None:
    document_id = upload_without_ingesting(client, b"# Title\nBody text.")
    assert client.get(f"/documents/{document_id}").json()["status"] == "pending"

    run_worker_until_idle()  # startup sweep finds it in the database and queues it

    assert client.get(f"/documents/{document_id}").json()["status"] == "ready"
    assert len(client.get(f"/documents/{document_id}/chunks").json()) == 1


def test_worker_finishes_a_document_left_in_processing_by_a_crash(client: TestClient) -> None:
    document_id = upload_without_ingesting(client, b"# Title\nAfter the crash.")
    set_status(document_id, "processing")  # as if a worker died mid-job

    run_worker_until_idle()

    assert client.get(f"/documents/{document_id}").json()["status"] == "ready"


def test_upload_succeeds_even_when_the_queue_is_down(client: TestClient) -> None:
    app.dependency_overrides[get_job_queue] = lambda: BrokenJobQueue()

    response = client.post("/documents", files={"file": ("notes.md", b"saved anyway")})

    assert response.status_code == 201
    assert response.json()["status"] == "pending"  # the sweep will pick it up later


def test_enqueueing_the_same_document_twice_creates_one_job() -> None:
    async def run() -> tuple[bool, bool]:
        queue = create_saq_queue(get_settings())
        await queue.connect()
        try:
            job_queue = JobQueue(queue)
            document_id = uuid.uuid4()
            return (
                await job_queue.enqueue_ingestion(document_id),
                await job_queue.enqueue_ingestion(document_id),
            )
        finally:
            await queue.disconnect()

    assert asyncio.run(run()) == (True, False)
