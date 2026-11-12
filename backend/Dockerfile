# ContextOS API and worker image (same image, different command).
#   API:    alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000  (default)
#   Worker: saq app.worker.worker_settings
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# uv, pinned to the version that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.6.3 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first: this layer is cached until pyproject.toml or uv.lock change, so a
# code-only change rebuilds in seconds.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# Settings resolve data paths relative to the repository root, which is / in the image:
# uploads, the embedding model cache and folder-connector sources all live under /data
# (a volume). Run as an unprivileged user.
RUN useradd --create-home --uid 1000 contextos && mkdir -p /data && chown contextos /data
USER contextos

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
