import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

pytestmark = pytest.mark.integration  # every test in this file needs the database

NOTES = b"# API decision\nWe chose REST over GraphQL.\n"


def upload(client: TestClient, filename: str, content: bytes):
    return client.post("/documents", files={"file": (filename, content)})


def test_upload_creates_pending_document_and_stores_file(client: TestClient) -> None:
    response = upload(client, "notes.md", NOTES)

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "notes.md"
    assert body["content_type"] == "text/markdown"
    assert body["size_bytes"] == len(NOTES)
    assert body["sha256"] == hashlib.sha256(NOTES).hexdigest()
    assert body["status"] == "pending"
    stored = get_settings().upload_dir / body["sha256"]
    assert stored.read_bytes() == NOTES


def test_uploading_same_bytes_twice_returns_409_with_existing_id(client: TestClient) -> None:
    first = upload(client, "notes.md", NOTES).json()

    second = upload(client, "renamed-copy.md", NOTES)

    assert second.status_code == 409
    assert second.json()["detail"]["document_id"] == first["id"]


def test_unsupported_extension_returns_415(client: TestClient) -> None:
    assert upload(client, "virus.exe", b"MZ...").status_code == 415


def test_empty_file_returns_400(client: TestClient) -> None:
    assert upload(client, "empty.txt", b"").status_code == 400


def test_file_over_limit_returns_413_and_leaves_no_files(client: TestClient) -> None:
    small_limit = get_settings().model_copy(update={"max_upload_bytes": 10})
    app.dependency_overrides[get_settings] = lambda: small_limit

    response = upload(client, "big.txt", b"x" * 11)

    assert response.status_code == 413
    assert list(small_limit.upload_dir.glob("*")) == []  # temp file was cleaned up


def test_list_returns_newest_first(client: TestClient) -> None:
    upload(client, "first.txt", b"one")
    upload(client, "second.txt", b"two")

    response = client.get("/documents")

    assert response.status_code == 200
    assert [doc["filename"] for doc in response.json()] == ["second.txt", "first.txt"]


def test_get_document_by_id(client: TestClient) -> None:
    created = upload(client, "notes.md", NOTES).json()

    assert client.get(f"/documents/{created['id']}").json() == created
    assert client.get(f"/documents/{uuid.uuid4()}").status_code == 404
    assert client.get("/documents/not-a-uuid").status_code == 422
