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
from app.embeddings import Embedder, get_embedder
from app.jobs import get_job_queue
from app.llm import get_llm
from app.main import app
from tests.helpers import FakeEmbedder, FakeLLM, InlineJobQueue

TEST_UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="contextos-test-uploads-"))
os.environ["POSTGRES_DB"] = "contextos_test"
os.environ["UPLOAD_DIR"] = str(TEST_UPLOAD_DIR)
os.environ["QUEUE_NAME"] = "contextos-test"

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
            # All tables in one statement: Postgres refuses to truncate a table that
            # another table's foreign key points at, unless they're truncated together.
            await connection.execute(text("TRUNCATE documents, chunks, conversations, messages"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_database() -> Iterator[None]:
    """Once per test run: create the test database and migrate it to the latest schema."""
    asyncio.run(_create_database_if_missing(get_settings().postgres_db))
    command.upgrade(Config(str(ALEMBIC_INI)), "head")
    yield
    shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def real_embedder() -> Embedder:
    """The real embedding model, loaded at most once per test run (for @pytest.mark.model tests)."""
    return Embedder(get_settings().model_cache_dir)


@pytest.fixture
def client(test_database: None) -> Iterator[TestClient]:
    """Per test: empty tables and uploads, then a client with the app's lifespan running.

    By default, jobs run inline, embeddings are fake (fast, no model) and the LLM is fake
    (no quota spent, ever). Tests needing the real thing override get_job_queue /
    get_embedder / get_llm.
    """
    asyncio.run(_empty_tables())
    shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)
    with TestClient(app) as test_client:
        fake_embedder = FakeEmbedder()
        inline_queue = InlineJobQueue(app.state.db_engine, fake_embedder)
        fake_llm = FakeLLM()
        app.dependency_overrides[get_job_queue] = lambda: inline_queue
        app.dependency_overrides[get_embedder] = lambda: fake_embedder
        app.dependency_overrides[get_llm] = lambda: fake_llm
        yield test_client
    app.dependency_overrides.clear()
