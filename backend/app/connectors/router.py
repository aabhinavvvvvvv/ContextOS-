"""HTTP routes for connectors. Syncing happens in the background worker."""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.connectors import service
from app.connectors.schemas import ConnectorCreate, ConnectorRead
from app.connectors.sources import SourceError
from app.db import SessionDep
from app.jobs import JobQueue, get_job_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["connectors"])
JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]


@router.get("")
async def list_connectors(session: SessionDep) -> list[ConnectorRead]:
    return [ConnectorRead.model_validate(c) for c in await service.list_connectors(session)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_connector(
    body: ConnectorCreate,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    job_queue: JobQueueDep,
) -> ConnectorRead:
    """Connect a GitHub repository ("owner/repo") or a folder inside FOLDER_ROOT. The first
    sync is queued right away; after that the worker syncs every 15 minutes."""
    try:
        connector = await service.create_connector(session, settings, body.kind, body.name)
    except SourceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except service.DuplicateConnectorError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    await _enqueue(job_queue, connector.id)
    return ConnectorRead.model_validate(connector)


@router.post("/{connector_id}/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_connector(connector_id: uuid.UUID, session: SessionDep, job_queue: JobQueueDep) -> dict:
    """Sync now (in the background). 202: accepted, check `last_status` later."""
    if not any(c.id == connector_id for c in await service.list_connectors(session)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector not found")
    return {"queued": await _enqueue(job_queue, connector_id)}


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(connector_id: uuid.UUID, session: SessionDep) -> None:
    """Disconnect a source and delete everything it synced."""
    if not await service.delete_connector(session, connector_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector not found")


async def _enqueue(job_queue: JobQueue, connector_id: uuid.UUID) -> bool:
    try:
        return await job_queue.enqueue_connector_sync(connector_id)
    except Exception:
        logger.exception("Could not queue sync for connector %s", connector_id)
        return False  # the 15-minute schedule will sync it
