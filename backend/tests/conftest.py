"""Shared test setup. pytest loads this file automatically before any test.

Tests never touch the development database or upload folder: the environment variables
below take priority over .env, so every get_settings() call during the test run sees them.
"""

import asyncio
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import build_database_url
from app.main import app

TEST_UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="contextos-test-uploads-"))
os.environ["POSTGRES_DB"] = "contextos_test"
os.environ["UPLOAD_DIR"] = str(TEST_UPLOAD_DIR)

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


async def _create_database_if_missing(name: str) -> None:
    # CREATE DATABASE must run outside a transaction (AUTOCOMMIT) and while
    # connected to some other database, here the built-in "postgres" one.
    admin_url = build_database_url(get_settings()).set(database="postgres")
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if not exists:
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


async def _empty_tables() -> None:
    engine = create_async_engine(build_database_url(get_settings()))
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE documents"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_database() -> Iterator[None]:
    """Once per test run: create the test database and migrate it to the latest schema."""
    asyncio.run(_create_database_if_missing(get_settings().postgres_db))
    command.upgrade(Config(str(ALEMBIC_INI)), "head")
    yield
    shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)


@pytest.fixture
def client(test_database: None) -> Iterator[TestClient]:
    """Per test: empty tables and uploads, then a client with the app's lifespan running."""
    asyncio.run(_empty_tables())
    shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
