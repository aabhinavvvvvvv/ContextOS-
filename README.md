# ContextOS

**Agentic RAG over live data and persistent memory.**

ContextOS is a personal knowledge and workflow assistant. It connects to your documents and GitHub, retrieves relevant evidence with hybrid (semantic + keyword) search, remembers past decisions, and runs multi-step workflows. Every answer cites its sources, and nothing consequential happens without your approval.

> Status: **Phase 1 complete** (backend and document ingestion). Next: Phase 2, embeddings and RAG. See [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md).

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
    ingestion/       extract text (md/txt/pdf) → chunk → embed → save
    search/          hybrid search: semantic (pgvector, HNSW) + keyword (tsvector, GIN), fused with RRF
    answers/         RAG: retrieve (hybrid search) → grounded prompt → LLM → verified citations
    llm.py           LLM client (Google Gemini via google-genai); the only module that knows the provider
    embeddings.py    local embedding model (fastembed, BAAI/bge-small-en-v1.5, 384 dims)
    jobs.py          Redis job queue (SAQ); the only place the API touches the queue library
    worker.py        background worker process: runs ingestion jobs, re-queues unfinished documents
  migrations/        Alembic database migrations
  tests/             pytest tests (integration tests use a separate contextos_test database)
data/uploads/        uploaded files, named by SHA-256 (gitignored)
data/models/         downloaded embedding model, ~65 MB, fetched on first use (gitignored)
docker-compose.yml   local Postgres (pgvector) and Redis
.env.example         template for local configuration
```

## Local development

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Git.

```bash
# 1. Configuration
cp .env.example .env            # then set POSTGRES_PASSWORD (and GEMINI_API_KEY for /ask)

# 2. Infrastructure (Postgres on localhost:5433, Redis on localhost:6380)
docker compose up -d
docker compose ps               # both services should be "healthy"

# 3. Backend
cd backend
uv sync                         # create .venv and install locked dependencies
uv run alembic upgrade head     # apply database migrations
uv run pytest                   # run tests (-m "not integration" to skip DB tests)
uv run uvicorn app.main:app --reload   # API on http://localhost:8000, docs at /docs

# 4. Worker (second terminal, from backend/): processes uploaded documents
uv run saq app.worker.worker_settings
```

Uploads are saved immediately and ingested by the worker. If the worker is stopped, jobs wait in Redis. If a job never reached Redis, the worker's startup and once-a-minute sweep re-queue every document the database still marks `pending` or `processing`.

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness: the process is up |
| `GET` | `/health/ready` | Readiness: Postgres is reachable (503 if not) |
| `POST` | `/documents` | Upload a `.md`, `.txt` or `.pdf` file (multipart field `file`). 201, or 409 for duplicates, 413 too large, 415 unsupported type |
| `GET` | `/documents?limit=50` | List documents, newest first |
| `GET` | `/documents/{id}` | One document (404 if unknown). `status`: `pending` → `processing` → `ready` / `failed` (with `error`) |
| `GET` | `/documents/{id}/chunks` | The document's text chunks in reading order, with `heading` (Markdown) or `page_number` (PDF) |
| `POST` | `/ask` `{"question": "...", "top_k": 5}` | Grounded answer: hybrid search, then the LLM answers **only** from the retrieved chunks, citing them as `[n]`. Returns `answer`, verified `citations`, token usage. 503 if no `GEMINI_API_KEY`, 429 if the free quota is used up, 502 on provider errors. |
| `GET` | `/search?q=...&limit=5&mode=hybrid` | Search chunks, best first, with citation fields. `mode=hybrid` (default): semantic + keyword merged by Reciprocal Rank Fusion, with each method's rank. `mode=semantic`: closest in meaning (score = cosine similarity). `mode=keyword`: Postgres full-text search with stemming, `"phrases"`, `or`, `-exclude` (score = ts_rank). Scores are only comparable within one mode. |

```bash
curl -F "file=@notes.md" http://localhost:8000/documents
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundations and project setup | ✅ |
| 1 | FastAPI backend, document ingestion, PostgreSQL, background worker | ✅ |
| 2 | Embeddings, hybrid retrieval, grounded answers with citations | ⏳ next |
| 3 | Persistent memory | |
| 4 | Agentic workflows with LangGraph | |
| 5 | Live connectors (GitHub, local docs) and MCP | |
| 6 | Controlled actions with human approval | |
| 7 | Frontend | |
| 8 | Evaluation, testing and observability | |
| 9 | Deployment | |
