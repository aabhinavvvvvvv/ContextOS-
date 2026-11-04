"""HTTP routes for documents: translate requests to service calls and errors to status codes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import Settings, get_settings
from app.db import get_engine, get_session
from app.documents import service
from app.documents.schemas import ChunkRead, DocumentRead
from app.ingestion.service import ingest_document

router = APIRouter(prefix="/documents", tags=["documents"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
EngineDep = Annotated[AsyncEngine, Depends(get_engine)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile,
    session: SessionDep,
    settings: SettingsDep,
    engine: EngineDep,
    background_tasks: BackgroundTasks,
) -> DocumentRead:
    """Upload a .md, .txt or .pdf file. It is stored and ingested in the background.

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
    # Runs after the response is sent, in this same process. Not durable: if the server
    # stops before it runs, the document stays "pending". A Redis job queue fixes this (1.7).
    background_tasks.add_task(ingest_document, engine, document.id, settings.upload_dir)
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
