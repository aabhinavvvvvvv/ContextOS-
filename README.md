# ContextOS

**Agentic RAG over live data and persistent memory.**

ContextOS is a personal knowledge and workflow assistant. It connects to your documents and GitHub, retrieves relevant evidence with hybrid (semantic + keyword) search, remembers past decisions, and runs multi-step workflows. Every answer cites its sources, and nothing consequential happens without your approval.

> Status: **Phase 1 in progress** (backend and document ingestion). See [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md).

## Architecture

```
React (TypeScript, Tailwind)
        │  HTTP/JSON + server-sent events
        ▼
FastAPI backend ──► LangGraph agent ──► LLM provider / MCP connectors (GitHub, docs)
        │                                 
        ├──► PostgreSQL + pgvector   documents, chunks, embeddings, memories, audit log
        └──► Redis                   job queue + cache ◄── background worker
```

## Repository layout

```
backend/             FastAPI application (Python 3.12, managed by uv)
  app/               application package
    documents/       upload and listing (router → service → models)
  migrations/        Alembic database migrations
  tests/             pytest tests (integration tests use a separate contextos_test database)
data/uploads/        uploaded files, named by SHA-256 (gitignored)
docker-compose.yml   local Postgres (pgvector) and Redis
.env.example         template for local configuration
```

## Local development

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Git.

```bash
# 1. Configuration
cp .env.example .env            # then set POSTGRES_PASSWORD

# 2. Infrastructure (Postgres on localhost:5433, Redis on localhost:6380)
docker compose up -d
docker compose ps               # both services should be "healthy"

# 3. Backend
cd backend
uv sync                         # create .venv and install locked dependencies
uv run alembic upgrade head     # apply database migrations
uv run pytest                   # run tests (-m "not integration" to skip DB tests)
uv run uvicorn app.main:app --reload   # API on http://localhost:8000, docs at /docs
```

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness: the process is up |
| `GET` | `/health/ready` | Readiness: Postgres is reachable (503 if not) |
| `POST` | `/documents` | Upload a `.md`, `.txt` or `.pdf` file (multipart field `file`). 201, or 409 for duplicates, 413 too large, 415 unsupported type |
| `GET` | `/documents?limit=50` | List documents, newest first |
| `GET` | `/documents/{id}` | One document (404 if unknown) |

```bash
curl -F "file=@notes.md" http://localhost:8000/documents
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundations and project setup | ✅ |
| 1 | FastAPI backend, document ingestion, PostgreSQL | ⏳ next |
| 2 | Embeddings, hybrid retrieval, grounded answers with citations | |
| 3 | Persistent memory | |
| 4 | Agentic workflows with LangGraph | |
| 5 | Live connectors (GitHub, local docs) and MCP | |
| 6 | Controlled actions with human approval | |
| 7 | Frontend | |
| 8 | Evaluation, testing and observability | |
| 9 | Deployment | |
