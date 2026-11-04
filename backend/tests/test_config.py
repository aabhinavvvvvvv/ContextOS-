import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_read_and_convert_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "alice")
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_DB", "testdb")
    monkeypatch.setenv("POSTGRES_PORT", "6543")

    # _env_file=None: ignore the developer's real .env so the test is isolated.
    settings = Settings(_env_file=None)

    assert settings.postgres_port == 6543  # converted from the string "6543"
    assert settings.postgres_host == "localhost"  # default used
    assert settings.postgres_password.get_secret_value() == "s3cret"
    assert "s3cret" not in repr(settings)  # never leaks into logs


def test_settings_fail_fast_when_required_value_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_USER", "alice")
    monkeypatch.setenv("POSTGRES_DB", "testdb")
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    with pytest.raises(ValidationError, match="postgres_password"):
        Settings(_env_file=None)
