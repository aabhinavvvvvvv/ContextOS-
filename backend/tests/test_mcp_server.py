"""The MCP server, exercised by a real MCP client connected in memory."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from mcp.client.client import Client

from app import mcp_server
from app.config import get_settings
from app.db import create_db_engine
from tests.helpers import FakeEmbedder, FakeLLM
from tests.test_answers import upload
from tests.test_memory_management import store_with


@pytest.mark.integration
def test_an_mcp_client_can_discover_and_use_contextos(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    upload(client)  # "We use FastAPI." / "REST, not GraphQL."
    store_with(client, {"kind": "task", "content": "The user needs to write the MCP docs."})
    monkeypatch.setattr(mcp_server.deps, "embedder", FakeEmbedder())
    monkeypatch.setattr(mcp_server.deps, "llm", FakeLLM("FastAPI [1]."))

    async def run() -> tuple:
        engine = create_db_engine(get_settings())
        monkeypatch.setattr(mcp_server.deps, "engine", engine)
        try:
            async with Client(mcp_server.server) as mcp:
                tools = await mcp.list_tools()
                search = await mcp.call_tool("search_knowledge", {"query": "FastAPI"})
                tasks = await mcp.call_tool("list_open_tasks", {})
                answer = await mcp.call_tool("ask", {"question": "Which framework do we use?"})
                memories = await mcp.read_resource("contextos://memories")
            return tools, search, tasks, answer, memories
        finally:
            await engine.dispose()

    tools, search, tasks, answer, memories = asyncio.run(run())

    assert {t.name for t in tools.tools} == {"search_knowledge", "ask", "list_open_tasks"}
    assert "We use FastAPI." in search.content[0].text
    assert tasks.content[0].text == "- The user needs to write the MCP docs."
    assert answer.content[0].text.startswith("FastAPI [1].\n\nSources:\n[1] notes.md")
    assert "[task] The user needs to write the MCP docs." in memories.contents[0].text
