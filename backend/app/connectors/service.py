"""Connector business logic: register sources, sync their items into documents.

A sync turns each source item into a document keyed by `source_key`:
    new item       → new document (then ingested: chunked + embedded)
    unchanged item → nothing (same SHA-256)
    changed item   → old document deleted (its chunks cascade), new one created
    folder file gone → its document deleted ("pruned")
"""

import hashlib
import io
import logging
import uuid
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.concurrency import run_in_threadpool

from app.actions.service import audit
from app.config import Settings
from app.connectors.sources import (
    SourceError,
    SourceItem,
    fetch_github,
    github_client,
    resolve_folder,
    scan_folder,
    valid_repo,
)
from app.documents.service import SUPPORTED_TYPES, _store_file
from app.embeddings import Embedder
from app.ingestion.service import ingest_document
from app.models import Connector, Document

logger = logging.getLogger(__name__)


class DuplicateConnectorError(Exception):
    pass


async def create_connector(session: AsyncSession, settings: Settings, kind: str, name: str) -> Connector:
    """Validate and register a source. Raises SourceError (invalid) or DuplicateConnectorError."""
    name = name.strip().strip("/")
    if kind == "github" and not valid_repo(name):
        raise SourceError("Repository must look like owner/name")
    if kind == "folder":
        await run_in_threadpool(resolve_folder, settings.folder_root, name)
    connector = Connector(id=uuid.uuid4(), kind=kind, name=name)
    session.add(connector)
    audit(session, "user", "connector.created", "connector", connector.id, {"kind": kind, "name": name})
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise DuplicateConnectorError(f"{kind} {name} is already connected") from None
    return connector


async def list_connectors(session: AsyncSession) -> list[Connector]:
    return list(await session.scalars(select(Connector).order_by(Connector.created_at)))


async def delete_connector(session: AsyncSession, connector_id: uuid.UUID) -> bool:
    """Remove a source and everything it synced (documents and chunks cascade)."""
    result = await session.execute(delete(Connector).where(Connector.id == connector_id))
    if result.rowcount:
        audit(session, "user", "connector.deleted", "connector", connector_id)
    await session.commit()
    return result.rowcount > 0


async def upsert_item(
    session: AsyncSession, connector: Connector, item: SourceItem, settings: Settings
) -> uuid.UUID | None:
    """Store one item as a document. Returns the new document's id, or None if nothing changed."""
    sha256 = hashlib.sha256(item.content).hexdigest()
    existing = await session.scalar(select(Document).where(Document.source_key == item.key))
    if existing is not None and existing.sha256 == sha256:
        return None
    if existing is not None:
        await session.delete(existing)  # changed: replace (chunks cascade in the database)
        await session.flush()
    if await session.scalar(select(Document.id).where(Document.sha256 == sha256)):
        return None  # identical content already exists (e.g. the same file uploaded by hand)
    content_type = SUPPORTED_TYPES.get(PurePath(item.filename).suffix.lower(), "text/markdown")
    await run_in_threadpool(_store_file, io.BytesIO(item.content), settings.upload_dir, settings.max_upload_bytes)
    document = Document(
        id=uuid.uuid4(),
        filename=item.filename,
        content_type=content_type,
        size_bytes=len(item.content),
        sha256=sha256,
        source=connector.kind,
        source_key=item.key,
        source_url=item.url,
        connector_id=connector.id,
    )
    session.add(document)
    return document.id


async def _read_source(connector: Connector, settings: Settings, http_transport: Any) -> list[SourceItem]:
    if connector.kind == "github":
        async with github_client(settings, http_transport) as client:
            return await fetch_github(client, connector.name, connector.last_synced_at)
    return await run_in_threadpool(scan_folder, settings.folder_root, connector.name, settings.max_upload_bytes)


async def sync_connector(
    engine: AsyncEngine,
    embedder: Embedder,
    settings: Settings,
    connector_id: uuid.UUID,
    *,
    http_transport: Any = None,
) -> int:
    """Sync one source: store changed items, then ingest them. Returns how many documents
    changed. Source errors (rate limit, not found, network) are recorded on the connector
    and the next scheduled sync tries again; they don't raise."""
    started = datetime.now(UTC)  # "since" for next time: items updated during this sync are re-fetched
    async with AsyncSession(engine, expire_on_commit=False) as session:
        connector = await session.get(Connector, connector_id)
    if connector is None:
        return 0
    try:
        items = await _read_source(connector, settings, http_transport)
        changed: list[uuid.UUID] = []
        async with AsyncSession(engine) as session:
            for item in items:
                document_id = await upsert_item(session, connector, item, settings)
                if document_id is not None:
                    changed.append(document_id)
            if connector.kind == "folder":  # a folder is listed in full, so missing = deleted
                await session.execute(
                    delete(Document).where(
                        Document.connector_id == connector.id,
                        Document.source_key.not_in([item.key for item in items]),
                    )
                )
            await session.execute(
                update(Connector)
                .where(Connector.id == connector.id)
                .values(last_synced_at=started, last_status="ok", last_error=None, documents_changed=len(changed))
            )
            await session.commit()
    except Exception as exc:  # rate limit, 404, network, bad file: recorded, retried on schedule
        logger.warning("Sync of %s %s failed: %s", connector.kind, connector.name, exc)
        async with AsyncSession(engine) as session:
            await session.execute(
                update(Connector)
                .where(Connector.id == connector.id)
                .values(last_status="error", last_error=str(exc)[:500])
            )
            await session.commit()
        return 0
    for document_id in changed:  # outside the transaction: each ingestion commits on its own
        await ingest_document(engine, document_id, settings.upload_dir, embedder)
    logger.info("Synced %s %s: %d document(s) changed", connector.kind, connector.name, len(changed))
    return len(changed)
