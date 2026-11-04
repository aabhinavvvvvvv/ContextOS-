# ContextOS

**Agentic RAG over live data and persistent memory.**

ContextOS is a personal knowledge and workflow assistant. It connects to your documents and GitHub, retrieves relevant evidence with hybrid (semantic + keyword) search, remembers past decisions, and runs multi-step workflows. Every answer cites its sources, and nothing consequential happens without your approval.

> Status: **Phase 0 complete** (foundations and project setup). See [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md).

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
  tests/             pytest tests
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
uv run pytest                   # run tests
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
