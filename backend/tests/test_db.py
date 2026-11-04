import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db import build_database_url, get_engine
from app.main import app


def test_database_url_escapes_special_characters() -> None:
    settings = Settings(
        _env_file=None,
        postgres_user="alice",
        postgres_password="p@ss:w/rd",
        postgres_db="contextos",
        postgres_port=5433,
    )

    url = build_database_url(settings)

    assert url.render_as_string(hide_password=False) == (
        "postgresql+asyncpg://alice:p%40ss%3Aw%2Frd@localhost:5433/contextos"
    )
    assert "p@ss" not in str(url)  # str() masks the password


def test_readiness_returns_503_when_database_unreachable() -> None:
    # Nothing listens on port 1, so the connection is refused immediately.
    unreachable = create_async_engine("postgresql+asyncpg://user:pw@127.0.0.1:1/db")
    app.dependency_overrides[get_engine] = lambda: unreachable
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unreachable"}


@pytest.mark.integration
def test_readiness_ok_when_database_reachable(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
