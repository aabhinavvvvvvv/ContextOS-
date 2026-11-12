"""Connectors: GitHub and folder sources synced into documents (fake GitHub, temp folder)."""

import asyncio
import uuid
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.connectors.service import sync_connector
from app.connectors.sources import SourceError, commit_item, issue_item, resolve_folder, valid_repo
from app.db import create_db_engine
from tests.helpers import FakeEmbedder, FakeLLM
from tests.test_answers import use_llm


def issue(number: int, title: str, body: str = "Body.", pr: bool = False) -> dict:
    item = {
        "number": number,
        "title": title,
        "state": "open",
        "user": {"login": "alice"},
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-10T10:00:00Z",
        "labels": [{"name": "bug"}],
        "html_url": f"https://github.com/acme/app/issues/{number}",
        "body": body,
    }
    if pr:
        item["pull_request"] = {}
    return item


COMMIT = {
    "sha": "a1b2c3d" + "0" * 33,
    "commit": {"message": "Fix login timeout\n\nRaise the session timeout to 30 minutes.",
               "author": {"name": "Bob", "date": "2026-11-09T08:00:00Z"}},
    "html_url": "https://github.com/acme/app/commit/a1b2c3d",
}


class FakeGitHub:
    """Answers /repos/acme/app/issues and /commits like GitHub's REST API, and records requests."""

    def __init__(self, issues: list[dict], commits: list[dict]) -> None:
        self.issues, self.commits = issues, commits
        self.requests: list[httpx2.Request] = []
        self.rate_limited = False

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.rate_limited:
            headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1794480000"}
            return httpx2.Response(403, headers=headers, json={"message": "API rate limit exceeded"})
        data = self.issues if request.url.path.endswith("/issues") else self.commits
        return httpx2.Response(200, json=data)

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)


def run_sync(connector_id: str, transport: httpx2.MockTransport | None = None) -> int:
    async def run() -> int:
        engine = create_db_engine(get_settings())
        try:
            return await sync_connector(
                engine, FakeEmbedder(), get_settings(), uuid.UUID(connector_id), http_transport=transport
            )
        finally:
            await engine.dispose()

    return asyncio.run(run())


def connect(client: TestClient, kind: str, name: str) -> dict:
    response = client.post("/connectors", json={"kind": kind, "name": name})
    assert response.status_code == 201, response.text
    return response.json()


def documents(client: TestClient) -> list[dict]:
    return client.get("/documents").json()


# --- pure functions -------------------------------------------------------------------


def test_github_items_become_readable_markdown_with_stable_keys() -> None:
    pr = issue_item("acme/app", issue(7, "Add caching", "Uses Redis.", pr=True))
    commit = commit_item("acme/app", COMMIT)

    assert pr.key == "github:acme/app#7" and pr.url == "https://github.com/acme/app/issues/7"
    assert pr.content.decode().startswith("# Pull request #7: Add caching\n")
    assert "State: open" in pr.content.decode() and "Labels: bug" in pr.content.decode()
    assert commit.key == "github:acme/app@" + COMMIT["sha"]
    assert commit.filename == "acme/app@a1b2c3d Fix login timeout.md"


def test_valid_repo_names() -> None:
    assert valid_repo("acme/app") and valid_repo("a-b/c.d_e")
    assert not valid_repo("acme") and not valid_repo("../etc/passwd") and not valid_repo("a/b/c")


def test_folders_outside_the_root_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    assert resolve_folder(tmp_path, "notes") == (tmp_path / "notes").resolve()
    for escape in ["../", "/etc", "notes/../../"]:
        with pytest.raises(SourceError):
            resolve_folder(tmp_path, escape)


# --- GitHub connector ---------------------------------------------------------------


@pytest.mark.integration
def test_github_sync_creates_updates_and_skips_unchanged_items(client: TestClient) -> None:
    github = FakeGitHub([issue(1, "Login fails"), issue(2, "Add caching", pr=True)], [COMMIT])
    connector = connect(client, "github", "acme/app")

    assert run_sync(connector["id"], github.transport) == 3
    docs = documents(client)
    assert sorted(d["source_url"] for d in docs) == [
        "https://github.com/acme/app/commit/a1b2c3d",
        "https://github.com/acme/app/issues/1",
        "https://github.com/acme/app/issues/2",
    ]
    assert {d["source"] for d in docs} == {"github"} and {d["status"] for d in docs} == {"ready"}
    assert "since" not in github.requests[0].url.params  # first sync: everything

    assert run_sync(connector["id"], github.transport) == 0  # nothing changed
    assert "since" in github.requests[-1].url.params  # later syncs ask only for changes

    github.issues = [issue(1, "Login fails", "Now with steps to reproduce.")]
    assert run_sync(connector["id"], github.transport) == 1
    docs = documents(client)
    assert len(docs) == 3  # replaced, not duplicated
    changed = next(d for d in docs if d["source_url"].endswith("/issues/1"))
    chunks = client.get(f"/documents/{changed['id']}/chunks").json()
    assert "steps to reproduce" in chunks[0]["text"]
    [status] = client.get("/connectors").json()
    assert (status["last_status"], status["documents_changed"]) == ("ok", 1)


@pytest.mark.integration
def test_a_rate_limited_sync_is_recorded_not_raised(client: TestClient) -> None:
    github = FakeGitHub([], [])
    github.rate_limited = True
    connector = connect(client, "github", "acme/app")

    assert run_sync(connector["id"], github.transport) == 0

    [status] = client.get("/connectors").json()
    assert status["last_status"] == "error" and "rate limit" in status["last_error"]


@pytest.mark.integration
def test_answers_cite_synced_items_with_a_link(client: TestClient) -> None:
    connector = connect(client, "github", "acme/app")
    run_sync(connector["id"], FakeGitHub([issue(1, "Login fails")], []).transport)
    use_llm(FakeLLM("Login fails [1]."))

    body = client.post("/ask", json={"question": "Which bug is open?"}).json()

    assert body["citations"][0]["source_url"] == "https://github.com/acme/app/issues/1"


@pytest.mark.integration
def test_connector_validation_and_deletion(client: TestClient) -> None:
    assert client.post("/connectors", json={"kind": "github", "name": "not-a-repo"}).status_code == 400
    assert client.post("/connectors", json={"kind": "folder", "name": "../../etc"}).status_code == 400
    connector = connect(client, "github", "acme/app")
    assert client.post("/connectors", json={"kind": "github", "name": "acme/app"}).status_code == 409
    run_sync(connector["id"], FakeGitHub([issue(1, "Login fails")], []).transport)
    assert len(documents(client)) == 1

    assert client.delete(f"/connectors/{connector['id']}").status_code == 204
    assert documents(client) == []  # everything it synced is gone
    assert client.delete(f"/connectors/{connector['id']}").status_code == 404


# --- Folder connector ---------------------------------------------------------------


@pytest.mark.integration
def test_folder_sync_adds_changes_and_prunes_files(client: TestClient) -> None:
    folder = get_settings().folder_root / f"notes-{uuid.uuid4().hex[:6]}"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.md").write_text("# Plan\nShip the MVP.\n")
    (folder / "sub" / "b.txt").write_text("Budget is tight.\n")
    (folder / ".secret.md").write_text("hidden\n")  # hidden: skipped
    (folder / "photo.png").write_bytes(b"\x89PNG")  # unsupported: skipped
    connector = connect(client, "folder", folder.name)

    assert run_sync(connector["id"]) == 2
    assert sorted(d["filename"] for d in documents(client)) == ["a.md", "sub/b.txt"]

    (folder / "a.md").write_text("# Plan\nShip the MVP on Friday.\n")
    (folder / "sub" / "b.txt").unlink()
    assert run_sync(connector["id"]) == 1
    assert [d["filename"] for d in documents(client)] == ["a.md"]

