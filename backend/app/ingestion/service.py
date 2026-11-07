"""Ingestion job: turn a stored upload into embedded chunks in the database.

Status flow: pending -> processing -> ready (chunks + embeddings saved) or failed (error saved).
"""

import logging
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.concurrency import run_in_threadpool

from app.embeddings import Embedder, chunk_embedding_text
from app.ingestion.chunking import Chunk, chunk_sections
from app.ingestion.extract import ExtractionError, extract_sections
from app.models import Document, DocumentChunk

logger = logging.getLogger(__name__)

UNFINISHED_STATUSES = ("pending", "processing")


def _build_chunks(
    path: Path, content_type: str, embedder: Embedder
) -> list[tuple[Chunk, list[float]]]:
    """Read, extract, chunk and embed. Blocking and CPU-heavy, so it runs in a worker thread."""
    chunks = chunk_sections(extract_sections(path.read_bytes(), content_type))
    vectors = embedder.embed_passages(
        [chunk_embedding_text(chunk.heading, chunk.text) for chunk in chunks]
    )
    return list(zip(chunks, vectors, strict=True))


async def ingest_document(
    engine: AsyncEngine, document_id: uuid.UUID, upload_dir: Path, embedder: Embedder
) -> None:
    """Extract, chunk and embed one document. Safe to call more than once (idempotent).

    A document still in "processing" (its worker crashed mid-job) is processed again.
    That's safe: chunks and the "ready" status are committed together, so a crash
    never leaves partial chunks behind.

    Runs outside any request, so it opens its own session instead of using the request's.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session:
        document = await session.get(Document, document_id)
        if document is None or document.status not in UNFINISHED_STATUSES:
            return  # deleted, or already finished by an earlier run

        document.status = "processing"
        await session.commit()

        try:
            embedded_chunks = await run_in_threadpool(
                _build_chunks, upload_dir / document.sha256, document.content_type, embedder
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
                embedding=vector,
            )
            for chunk, vector in embedded_chunks
        )
        document.status = "ready"
        # One transaction: the chunks and the "ready" status are saved together or not at all.
        await session.commit()
        logger.info("Ingested document %s into %d chunks", document_id, len(embedded_chunks))


async def unfinished_document_ids(session: AsyncSession) -> list[uuid.UUID]:
    result = await session.scalars(
        select(Document.id).where(Document.status.in_(UNFINISHED_STATUSES))
    )
    return list(result)


async def _mark_failed(session: AsyncSession, document: Document, error: str) -> None:
    logger.warning("Ingestion failed for document %s: %s", document.id, error)
    document.status = "failed"
    document.error = error
    await session.commit()
