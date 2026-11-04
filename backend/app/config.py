"""Application settings, loaded from environment variables and the repo-root .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> parents[0] = app/, parents[1] = backend/, parents[2] = repository root
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    # Real environment variables take priority over .env; a missing .env file is fine.
    # extra="ignore": .env may hold variables meant for other tools (e.g. Docker Compose).
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    redis_host: str = "localhost"
    redis_port: int = 6379

    # Uploaded files are stored here, named by their SHA-256 hash.
    upload_dir: Path = REPO_ROOT / "data" / "uploads"
    max_upload_bytes: int = 25 * 1024 * 1024  # 25 MiB


@lru_cache
def get_settings() -> Settings:
    """Build the settings once, then return the same object on every call."""
    return Settings()
