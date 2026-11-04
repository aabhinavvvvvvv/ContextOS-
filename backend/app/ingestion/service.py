"""Ingestion job: turn a stored upload into chunks in the database.

Status flow: pending -> processing -> ready (chunks saved) or failed (error saved).
"""

import logging
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.concurrency import run_in_threadpool

from app.ingestion.chunking import Chunk, chunk_sections
from app.ingestion.extract import ExtractionError, extract_sections
from app.models import Document, DocumentChunk

logger = logging.getLogger(__name__)


def _build_chunks(path: Path, content_type: str) -> list[Chunk]:
    """Blocking and CPU-heavy (PDF parsing), so it runs in a worker thread."""
    return chunk_sections(extract_sections(path.read_bytes(), content_type))


async def ingest_document(engine: AsyncEngine, document_id: uuid.UUID, upload_dir: Path) -> None:
    """Extract and chunk one document. Safe to call more than once (idempotent).

    Runs outside any request, so it opens its own session instead of using the request's.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session:
        document = await session.get(Document, document_id)
        if document is None or document.status != "pending":
            return  # deleted, or already handled by an earlier run

        document.status = "processing"
        await session.commit()

        try:
            chunks = await run_in_threadpool(
                _build_chunks, upload_dir / document.sha256, document.content_type
            )
        except (ExtractionError, OSError) as exc:
            # Expected failures: bad file content, or the stored file is missing.
            await _mark_failed(session, document, str(exc))
            return
        except Exception:
            # A bug. Record it instead of leaving the document stuck in "processing".
            logger.exception("Unexpected error ingesting document %s", document_id)
            await _mark_failed(session, document, "Internal error during ingestion")
            return

        session.add_all(
            DocumentChunk(
                document_id=document.id,
                chunk_index=chunk.index,
                text=chunk.text,
                heading=chunk.heading,
                page_number=chunk.page_number,
            )
            for chunk in chunks
        )
        document.status = "ready"
        # One transaction: the chunks and the "ready" status are saved together or not at all.
        await session.commit()
        logger.info("Ingested document %s into %d chunks", document_id, len(chunks))


async def _mark_failed(session: AsyncSession, document: Document, error: str) -> None:
    logger.warning("Ingestion failed for document %s: %s", document.id, error)
    document.status = "failed"
    document.error = error
    await session.commit()
