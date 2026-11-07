"""Service-level search tests: things the HTTP layer can't control (planner settings)."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import create_db_engine
from app.search.service import semantic_search
from tests.helpers import FakeEmbedder

pytestmark = pytest.mark.integration


def test_semantic_search_returns_more_than_40_results_when_the_hnsw_index_is_used(
    client: TestClient,
) -> None:
    sections = "".join(f"# Section {i}\nBody text number {i}.\n\n" for i in range(60))
    client.post("/documents", files={"file": ("many.md", sections.encode())})

    async def run() -> int:
        engine = create_db_engine(get_settings())
        try:
            async with AsyncSession(engine) as session:
                # Force the HNSW index (with 60 rows the planner would rather scan the table).
                await session.execute(text("SET LOCAL enable_seqscan = off"))
                hits = await semantic_search(session, FakeEmbedder().vector("query"), limit=50)
                return len(hits)
        finally:
            await engine.dispose()

    # pgvector's index scan stops at hnsw.ef_search candidates (default 40).
    assert asyncio.run(run()) == 50
