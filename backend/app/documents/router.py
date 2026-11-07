"""HTTP routes for documents: translate requests to service calls and errors to status codes."""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status

from app.config import Settings, get_settings
from app.db import SessionDep
from app.documents import service
from app.documents.schemas import ChunkRead, DocumentRead
from app.jobs import JobQueue, get_job_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile,
    session: SessionDep,
    settings: SettingsDep,
    job_queue: JobQueueDep,
) -> DocumentRead:
    """Upload a .md, .txt or .pdf file. It is stored and ingested by the background worker.

    The response shows status `pending`; poll `GET /documents/{id}` until `ready` or `failed`.
    """
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file has no filename")
    try:
        document = await service.create_document(
            session,
            filename=file.filename,
            source=file.file,
            upload_dir=settings.upload_dir,
            max_bytes=settings.max_upload_bytes,
        )
    except service.UnsupportedFileTypeError:
        allowed = ", ".join(sorted(service.SUPPORTED_TYPES))
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported file type. Allowed: {allowed}"
        )
    except service.EmptyFileError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")
    except service.FileTooLargeError:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"File is larger than {settings.max_upload_bytes} bytes",
        )
    except service.DuplicateDocumentError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "This file was already uploaded", "document_id": str(exc.existing.id)},
        )
    try:
        await job_queue.enqueue_ingestion(document.id)
    except Exception:
        # Never fail an upload because the queue is down: the document is already saved
        # as "pending", and the worker's periodic sweep will queue it later.
        logger.exception("Could not enqueue ingestion for document %s", document.id)
    return DocumentRead.model_validate(document)


@router.get("")
async def list_documents(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[DocumentRead]:
    """List documents, newest first."""
    documents = await service.list_documents(session, limit=limit)
    return [DocumentRead.model_validate(doc) for doc in documents]


@router.get("/{document_id}")
async def read_document(document_id: uuid.UUID, session: SessionDep) -> DocumentRead:
    document = await service.get_document(session, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return DocumentRead.model_validate(document)


@router.get("/{document_id}/chunks")
async def read_document_chunks(document_id: uuid.UUID, session: SessionDep) -> list[ChunkRead]:
    """The document's chunks in reading order (empty until ingestion is `ready`)."""
    if await service.get_document(session, document_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    chunks = await service.list_chunks(session, document_id)
    return [ChunkRead.model_validate(chunk) for chunk in chunks]
