"""Database engine setup (SQLAlchemy async + asyncpg)."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

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


async def get_session(
    engine: Annotated[AsyncEngine, Depends(get_engine)],
) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one database session per request, closed when the request ends."""
    # expire_on_commit=False keeps loaded attributes readable after commit. Otherwise
    # SQLAlchemy would reload them implicitly, which is an error in async code.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
