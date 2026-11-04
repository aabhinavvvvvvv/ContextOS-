import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import create_db_engine
from app.ingestion.service import ingest_document
from tests.helpers import make_pdf

pytestmark = pytest.mark.integration

MARKDOWN = b"""# Architecture
We use FastAPI.

## API
REST, not GraphQL.
"""


def upload_and_fetch(client: TestClient, filename: str, content: bytes) -> dict:
    """Upload, then re-read the document. In tests the job runs inline (InlineJobQueue)."""
    created = client.post("/documents", files={"file": (filename, content)})
    assert created.status_code == 201
    assert created.json()["status"] == "pending"  # the response is sent before ingestion runs
    return client.get(f"/documents/{created.json()['id']}").json()


def run_ingestion(document_id: str) -> None:
    async def run() -> None:
        engine = create_db_engine(get_settings())
        try:
            await ingest_document(engine, uuid.UUID(document_id), get_settings().upload_dir)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_markdown_upload_becomes_ready_with_chunks(client: TestClient) -> None:
    document = upload_and_fetch(client, "notes.md", MARKDOWN)

    assert document["status"] == "ready"
    assert document["error"] is None
    chunks = client.get(f"/documents/{document['id']}/chunks").json()
    assert [(c["chunk_index"], c["heading"], c["text"]) for c in chunks] == [
        (0, "Architecture", "We use FastAPI."),
        (1, "Architecture > API", "REST, not GraphQL."),
    ]


def test_pdf_chunks_keep_page_numbers(client: TestClient) -> None:
    document = upload_and_fetch(client, "adr.pdf", make_pdf(["Decision: REST", "Status: accepted"]))

    chunks = client.get(f"/documents/{document['id']}/chunks").json()
    assert [(c["page_number"], c["text"]) for c in chunks] == [
        (1, "Decision: REST"),
        (2, "Status: accepted"),
    ]


def test_unreadable_file_is_marked_failed_with_reason(client: TestClient) -> None:
    document = upload_and_fetch(client, "broken.txt", b"\xff\xfe\xfa not utf-8")

    assert document["status"] == "failed"
    assert "UTF-8" in document["error"]
    assert client.get(f"/documents/{document['id']}/chunks").json() == []


def test_running_ingestion_again_does_not_duplicate_chunks(client: TestClient) -> None:
    document = upload_and_fetch(client, "notes.md", MARKDOWN)

    run_ingestion(document["id"])  # a second run, e.g. a retried job

    assert len(client.get(f"/documents/{document['id']}/chunks").json()) == 2


def test_chunks_of_unknown_document_returns_404(client: TestClient) -> None:
    assert client.get(f"/documents/{uuid.uuid4()}/chunks").status_code == 404
