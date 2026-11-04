"""FastAPI application entry point.

Run locally (from backend/):  uv run uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="ContextOS API", version="0.1.0")


class HealthResponse(BaseModel):
    status: str
    version: str


@app.get("/health")
async def health() -> HealthResponse:
    """Liveness check: the process is up and able to serve requests."""
    return HealthResponse(status="ok", version=app.version)
