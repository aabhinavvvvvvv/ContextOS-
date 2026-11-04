"""Database engine setup (SQLAlchemy async + asyncpg)."""

from fastapi import Request
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings


def build_database_url(settings: Settings) -> URL:
    # URL.create escapes special characters in the password; an f-string would not.
    return URL.create(
        drivername="postgresql+asyncpg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def create_db_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        build_database_url(settings),
        # Test each pooled connection before handing it out, replacing dead ones
        # (e.g. after a Postgres restart) instead of failing the request.
        pool_pre_ping=True,
        # Give up connecting after 5s instead of asyncpg's default 60s.
        connect_args={"timeout": 5},
    )


def get_engine(request: Request) -> AsyncEngine:
    """FastAPI dependency: the engine created at startup (see lifespan in main.py)."""
    return request.app.state.db_engine
