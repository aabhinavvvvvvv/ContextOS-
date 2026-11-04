# ContextOS Project Progress

Session-continuity log. A new session should read this file, then CLAUDE.md, then continue from **Next step**.

## Current state

- **Phase:** 1 in progress. 1.1 (FastAPI + health endpoint) is done.
- **Next step:** Phase 1.2. Typed settings loaded from `.env` with pydantic-settings, then connect to Postgres.

## Completed milestones

### Phase 0: Foundations (2026-10-30 → 2026-11-04)
- [x] Architecture walkthrough, example agentic workflow, tech stack overview
- [x] Environment check, Docker Desktop WSL integration enabled, `init.defaultBranch=main`
- [x] Git repo initialized, `.gitignore`, connected to GitHub over SSH
- [x] `.env.example` / `.env` (env vars, `.env` gitignored)
- [x] `docker-compose.yml`: Postgres 17 + pgvector 0.8.0, Redis 7.4, named volumes, health checks
- [x] `backend/` scaffolded with uv; pytest dev dependency; smoke tests pass
- [x] README and this file

### Phase 1: Backend and document ingestion
- [x] 1.1 FastAPI app (`app/main.py`), `GET /health` with Pydantic response model, TestClient tests (2026-11-04)

## Pending (Phase 1)
- [ ] Settings loaded from env (pydantic-settings)
- [ ] PostgreSQL connection (SQLAlchemy async) + migrations (Alembic), enable `vector` extension
- [ ] Document upload endpoint
- [ ] Parsing, text extraction, chunking, metadata
- [ ] Background processing via Redis queue

## Decisions log
| Decision | Reason |
|---|---|
| Host ports 5433 (Postgres) / 6380 (Redis) | Avoid clashing with other local projects on the defaults |
| pgvector inside Postgres, not a separate vector DB | One datastore, joins with metadata, free |
| uv for Python | Fast, lockfile, manages venv |
| Pinned image tags | `latest` changes silently and isn't reproducible |
| Code lives in `backend/app/`, tests in `backend/tests/` | Keeps app code separate from tooling; `pythonpath = ["."]` in pytest config |
| LLM provider TBD (free tier), behind one module | No paid API assumed; swappable by config |
| Routes stay in `main.py` until a second resource exists | Add APIRouter modules when `/documents` arrives; avoid premature structure |
| `httpx2` (not `httpx`) as the test-client dependency | Starlette 1.x deprecates `httpx` for its TestClient |

## Environment notes
- Develop inside WSL Ubuntu 24.04. Open the project with `code .` from the Ubuntu terminal.
- Docker Desktop on Windows with WSL integration.
- Python 3.12.3, uv 0.6.3, Node 22 (nvm), npm. pnpm is broken; use npm.

## Learning notes
- The user prefers that Claude writes the code and runs the commands, explaining each file, syntax and output. Quizzes are optional.
- Covered so far: client/server, ports and port mapping, Git internals (snapshots, staging, commits, branches, remotes, SSH), `.gitignore` syntax, env vars, Docker images/containers/volumes/health checks, Compose, venvs, pyproject/lockfiles, uv, HTTP (methods, status codes, headers, bodies), JSON, REST, ASGI/uvicorn/FastAPI roles, decorators and routing, Pydantic response models, OpenAPI/docs, TestClient.
