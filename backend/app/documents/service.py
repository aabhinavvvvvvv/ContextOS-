"""Document business logic: validation, file storage and database writes.

Knows nothing about HTTP, so the API, the background worker and the agent can all use it.
"""

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models import Document, DocumentChunk

# File extensions we can parse (Phase 1.6), mapped to the content type we record.
SUPPORTED_TYPES = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}
READ_CHUNK_BYTES = 1024 * 1024  # copy uploads 1 MiB at a time


class UnsupportedFileTypeError(Exception):
    pass


class EmptyFileError(Exception):
    pass


class FileTooLargeError(Exception):
    pass


class DuplicateDocumentError(Exception):
    def __init__(self, existing: Document) -> None:
        super().__init__(f"Document already exists: {existing.id}")
        self.existing = existing


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    size_bytes: int


def _store_file(source: BinaryIO, upload_dir: Path, max_bytes: int) -> StoredFile:
    """Copy an upload to disk while hashing it and enforcing the size limit.

    Blocking (plain file I/O), so callers in async code must run it in a thread.
    Files are named by their hash, so the user-supplied filename never touches the filesystem.
    """
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_dir / f".incoming-{uuid.uuid4()}"
    digest = hashlib.sha256()
    size = 0
    try:
        with temp_path.open("wb") as out:
            while chunk := source.read(READ_CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLargeError(f"File exceeds {max_bytes} bytes")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise EmptyFileError("File is empty")
        sha256 = digest.hexdigest()
        # Atomic rename: readers see either no file or the complete file, never half of one.
        os.replace(temp_path, upload_dir / sha256)
    finally:
        temp_path.unlink(missing_ok=True)
    return StoredFile(sha256=sha256, size_bytes=size)


async def create_document(
    session: AsyncSession,
    *,
    filename: str,
    source: BinaryIO,
    upload_dir: Path,
    max_bytes: int,
) -> Document:
    content_type = SUPPORTED_TYPES.get(Path(filename).suffix.lower())
    if content_type is None:
        raise UnsupportedFileTypeError(filename)

    stored = await run_in_threadpool(_store_file, source, upload_dir, max_bytes)

    document = Document(
        filename=filename,
        content_type=content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )
    session.add(document)
    try:
        await session.commit()
    except IntegrityError:
        # The unique constraint on sha256 is the source of truth for duplicates.
        # Checking first and inserting second would race with a concurrent upload.
        await session.rollback()
        existing = await session.scalar(
            select(Document).where(Document.sha256 == stored.sha256)
        )
        if existing is None:
            raise  # a different integrity problem, not a duplicate
        raise DuplicateDocumentError(existing) from None
    return document


async def list_documents(session: AsyncSession, *, limit: int) -> list[Document]:
    result = await session.scalars(
        select(Document).order_by(Document.created_at.desc(), Document.id).limit(limit)
    )
    return list(result)


async def get_document(session: AsyncSession, document_id: uuid.UUID) -> Document | None:
    return await session.get(Document, document_id)


async def list_chunks(session: AsyncSession, document_id: uuid.UUID) -> list[DocumentChunk]:
    result = await session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
    )
    return list(result)
