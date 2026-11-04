"""HTTP routes for documents: translate requests to service calls and errors to status codes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.documents import service
from app.documents.schemas import DocumentRead

router = APIRouter(prefix="/documents", tags=["documents"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile, session: SessionDep, settings: SettingsDep
) -> DocumentRead:
    """Upload a .md, .txt or .pdf file. It is stored and queued for ingestion (status `pending`)."""
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
