"""FastAPI application entry point.

Run locally (from backend/):  uv run uvicorn app.main:app --reload
Uploaded documents are ingested by the worker, a separate process:
    uv run saq app.worker.worker_settings
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db import create_db_engine, get_engine
from app.documents.router import router as documents_router
from app.jobs import JobQueue, create_saq_queue
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Startup: one database connection pool and one queue connection for the whole process.
    app.state.db_engine = create_db_engine(settings)
    saq_queue = create_saq_queue(settings)
    await saq_queue.connect()  # lazy: does not fail if Redis is down right now
    app.state.job_queue = JobQueue(saq_queue)
    yield
    # Shutdown: close connections cleanly.
    await saq_queue.disconnect()
    await app.state.db_engine.dispose()


app = FastAPI(title="ContextOS API", version="0.1.0", lifespan=lifespan)
app.include_router(documents_router)


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    database: str


@app.get("/health")
async def health() -> HealthResponse:
    """Liveness check: the process is up and able to serve requests."""
    return HealthResponse(status="ok", version=app.version)


@app.get("/health/ready")
async def readiness(
    response: Response,
    engine: Annotated[AsyncEngine, Depends(get_engine)],
) -> ReadinessResponse:
    """Readiness check: the services the app depends on (currently Postgres) are reachable."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        logger.warning("Database readiness check failed", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="unavailable", database="unreachable")
    return ReadinessResponse(status="ok", database="ok")
