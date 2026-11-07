# ContextOS Project Progress

Session-continuity log. A new session should read this file, then CLAUDE.md, then continue from **Next step**.

## Current state

- **Phase:** Phase 2 in progress. 2.1a (embedding model chosen + `app/embeddings.py`) is done.
- **Next step:** Phase 2.1b. Add the `pgvector` Python package; a migration adding `embedding vector(384) NOT NULL` to `chunks` (chunks are derived data, so the migration deletes existing chunks and resets `ready` docs to `pending` for the worker to re-ingest); load the `Embedder` once in the worker's startup ctx and embed chunks (via `chunk_embedding_text`) in a thread before the atomic commit; tests use a fake embedder (same pattern as `InlineJobQueue`).
- **Running locally:** Docker Desktop → `docker compose up -d` → from `backend/`: `uv run uvicorn app.main:app --reload` and, in a second terminal, `uv run saq app.worker.worker_settings`.

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
- [x] 1.2 `app/config.py`: typed `Settings` (pydantic-settings) reading the repo-root `.env`, `SecretStr` password, cached `get_settings()` (2026-11-04)
- [x] 1.3 `app/db.py`: async engine (asyncpg) created in the FastAPI `lifespan`, injected via `Depends(get_engine)`, `pool_pre_ping`, 5s connect timeout; `GET /health/ready` returns 503 when the DB is down; `integration` pytest marker (2026-11-04)
- [x] 1.4 Alembic (async template, URL from Settings, date-prefixed files); `app/models.py` with `Base` + naming convention + `Document`; migrations `b7598770d98e` (vector extension) and `b914679628b7` (documents table); verified constraints and the downgrade/upgrade round trip (2026-11-04)
- [x] 1.5 `app/documents/` (router, service, schemas): `POST /documents` (multipart, content-addressed storage in `data/uploads/<sha256>`, 201/400/409/413/415), `GET /documents`, `GET /documents/{id}`; `get_session` dependency; `tests/conftest.py` with an isolated `contextos_test` DB + temp upload dir; 14 tests pass (2026-11-05)
- [x] 1.6a `app/ingestion/extract.py` (md split by heading path, ignoring `#` inside code fences; txt; pdf per page via pypdf; normalization incl. NUL removal; `ExtractionError`) and `app/ingestion/chunking.py` (structure-aware, recursive paragraph → sentence → word split, overlap; defaults 1500/200 chars); 15 unit tests, 29 total (2026-11-05)
- [x] 1.6b `DocumentChunk` model + migration `9d4dec045cfc` (`chunks`, FK with ON DELETE CASCADE, UNIQUE(document_id, chunk_index)); naming convention now uses `column_0_N_name` for unique constraints; `app/ingestion/service.py` `ingest_document` (idempotent, atomic, failure reasons saved); triggered via `BackgroundTasks`; `GET /documents/{id}/chunks`; 34 tests pass (2026-11-05)
- [x] 1.7 Durable jobs with SAQ on Redis: `app/jobs.py` (`JobQueue` wrapper, key-based dedupe, timeout 300s, 3 attempts with backoff), `app/worker.py` (separate process; startup + every-minute sweep re-queues `pending`/`processing` docs), upload never fails if Redis is down, `app/logging_config.py`; ingestion guard now also re-runs `processing`; tests use an `InlineJobQueue` fake plus real-worker burst tests; 38 tests pass. Live demo: README rescued after a day stuck in `pending`; a job survived a worker restart (2026-11-05)

### Phase 2: Embeddings and RAG
- [x] 2.1a fastembed (ONNX runtime, not PyTorch) + `BAAI/bge-small-en-v1.5` (384 dims, 512 tokens), chosen by measuring 5 models on 10 questions about our own docs; `app/embeddings.py` (`Embedder`, `chunk_embedding_text`); models cached in `data/models` (65 MB); `model` pytest marker; 41 tests pass (2026-11-07)

## Pending (Phase 2: Embeddings and RAG)
- [ ] 2.1b Store vectors (`vector(384)` column) and embed chunks in the worker
- [ ] Semantic search (pgvector, cosine distance, index)
- [ ] Keyword search (Postgres full-text, `tsvector`)
- [ ] Hybrid search (merge rankings, e.g. reciprocal rank fusion)
- [ ] Context construction + LLM answer generation (choose a free-tier provider, behind one module)
- [ ] Source citations

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
| One `.env` at repo root, located via `Path(__file__)` | Shared by Compose and backend; works from any working directory |
| `SecretStr` for passwords and keys | Masked in repr/logs; requires explicit `.get_secret_value()` |
| Separate liveness (`/health`) and readiness (`/health/ready`) | A DB outage should stop traffic, not trigger app restarts |
| `pool_pre_ping=True` | Observed stale pooled connections failing requests after a Postgres restart |
| Engine in `lifespan` + `Depends`, not a module global | Clean startup/shutdown; tests swap it via `dependency_overrides` |
| Integration tests marked `@pytest.mark.integration` | `pytest -m "not integration"` runs without Docker |
| Stay on Python, not Node/Express (asked 2026-11-04) | AI ecosystem depth: parsing, local embeddings, reranking, eval; LangGraph Python is the primary implementation |
| UUID primary keys | Not enumerable in URLs, can be generated in the app, unique across sources |
| `status` as text + CHECK, not a Postgres ENUM | Adding a value later is a trivial migration; ENUM changes need `ALTER TYPE` |
| `sha256` unique on documents | The database itself rejects duplicate files (idempotent uploads) |
| All timestamps `timestamptz` | Phase 3 recency and conflict logic needs correct times |
| Workflow: review every autogenerated migration, `alembic check` for drift | Autogenerate can't tell a rename from drop+add |
| Feature folders (`app/documents/`) with router + service; no repository layer yet | Service is reusable by the worker/agent; queries are too small to need wrapping. Add repositories when queries get complex (hybrid search). |
| Files stored as `upload_dir/<sha256>` | User filenames never reach the filesystem (no path traversal); duplicates map to the same file |
| Duplicates detected by catching `IntegrityError` on insert, not check-then-insert | Check-then-insert races under concurrent uploads |
| Blocking file I/O runs via `run_in_threadpool` | Keeps the event loop free |
| Tests use DB `contextos_test` (env override in conftest) and TRUNCATE before each test | Tests can never touch dev data |
| Known limit: Starlette spools the whole upload before our size check | Enforce the body size at the reverse proxy in Phase 9 |
| pypdf for PDFs (not PyMuPDF) | Pure Python, BSD license; PyMuPDF is AGPL. Scanned PDFs are rejected (no OCR yet). |
| Chunk sizes in characters (1500 max, 200 overlap) | No tokenizer until Phase 2; ~4 chars/token; tune in Phase 8 |
| Chunks never cross headings or pages | Precise citations. Observed: small Markdown sections make tiny chunks (31 chars in CLAUDE.md). Plan: prepend the heading path when embedding (Phase 2); measure merging in Phase 8. |
| ORM model named `DocumentChunk` (table `chunks`) | Avoids a name clash with the `Chunk` dataclass in `ingestion/chunking.py` |
| No ORM `relationship()` between Document and DocumentChunk | Implicit lazy loading breaks in async code; query chunks explicitly |
| Ingestion opens its own session from the engine | Runs after the request ends, when the request's session is already closed |
| Broad `except Exception` only at the job boundary | Logs the bug and marks the document failed instead of leaving it stuck in "processing" |
| `BackgroundTasks` replaced by SAQ in 1.7 | `BackgroundTasks` was not durable (lost on restart), had no retries, and competed with API requests for CPU |
| SAQ for jobs (not arq / RQ / Celery) | arq's README says "maintenance only mode"; RQ workers are synchronous; SAQ is async-native and actively released. Smaller community, so isolated behind `JobQueue`. |
| Database is the source of truth; the queue only delivers | The worker sweep re-queues unfinished documents, so lost or never-sent jobs are recovered |
| Upload returns 201 even if enqueue fails | The document is saved; the sweep picks it up |
| No worker shutdown grace period (SAQ default) | Measured: any grace period makes every idle shutdown wait the full period, because SAQ also waits on its own loops. Jobs are crash-safe (atomic + idempotent), so interruption is safe. Revisit in Phase 9. |
| Redis URL with `socket_connect_timeout=2` only, no `socket_timeout` | The worker's blocking dequeue must be allowed to wait longer than any read timeout |
| fastembed (ONNX) instead of sentence-transformers | sentence-transformers needs torch (539 MB wheel + CUDA libraries); whole venv with fastembed is 283 MB |
| `BAAI/bge-small-en-v1.5` | Measured 2026-11-07 (top-1/top-3 of 10, peak RAM): bge-small 8/10 10/10 646 MB; granite-small-r2 8/10 10/10 1314 MB; nomic-v1.5-Q (768d) 8/10 10/10 1797 MB; jina-v2-small 6/10 8/10; snowflake-arctic-s 3/10 5/10. all-MiniLM-L6-v2 excluded (256-token limit). Tiny eval; redo properly in Phase 8. |
| Embedding model is a code constant, not an env setting | Changing it requires re-embedding everything (and maybe a migration), so it should be a deliberate code change |
| Embed `heading + "\n\n" + text` | Contextual header fixes tiny Markdown chunks |

## Environment notes
- Develop inside WSL Ubuntu 24.04. IDE: **Antigravity IDE**. Open the project via Command Palette → `Remote-WSL: Connect to WSL using Distro...` → Ubuntu-24.04 → open `/home/aabhinavvvvvvv/personal_projects/contextOS`. Install the Python and Pyrefly extensions "in WSL", then select the interpreter `backend/.venv/bin/python`.
- Docker Desktop must be running (WSL reports "docker could not be found" otherwise), then `docker compose up -d`.
- Workflow: Claude writes and runs the code and commits + pushes after each milestone. Before every push, scan git history for the `.env` password (print only lengths, never values).
- Docker Desktop on Windows with WSL integration.
- Python 3.12.3, uv 0.6.3, Node 22 (nvm), npm. pnpm is broken; use npm.

## Learning notes
- The user prefers that Claude writes the code and runs the commands, explaining each file, syntax and output. Quizzes are optional.
- Covered so far: client/server, ports and port mapping, Git internals (snapshots, staging, commits, branches, remotes, SSH), `.gitignore` syntax, env vars, Docker images/containers/volumes/health checks, Compose, venvs, pyproject/lockfiles, uv, HTTP (methods, status codes, headers, bodies), JSON, REST, ASGI/uvicorn/FastAPI roles, decorators and routing, Pydantic response models, OpenAPI/docs, TestClient, pydantic-settings, fail-fast config, SecretStr, `lru_cache`, `pathlib`, pytest `monkeypatch` / `raises`, async/await and the event loop, `async with`, connection pools, SQLAlchemy engine/URL/`text()`, lifespan and `yield`, dependency injection (`Depends`, `Annotated`), liveness vs readiness, timeouts, zombie processes, `pkill -f` self-match, tables/columns/types/keys, constraints (NOT NULL, UNIQUE, CHECK), indexes, ORM, `DeclarativeBase` / `Mapped` / `mapped_column`, `default` vs `server_default`, naming conventions, migrations / revision chain / `alembic_version`, autogenerate + review, `alembic check`, transactions and ROLLBACK, multipart/form-data, `UploadFile`, sessions as units of work, `expire_on_commit`, `yield` dependencies and sub-dependencies, `APIRouter`/`include_router`, path and query parameters with validation, `HTTPException`, custom exceptions, `@dataclass(frozen=True)`, the walrus operator `:=`, hashing (SHA-256), atomic rename, `run_in_threadpool`, the race between check-then-insert, pytest fixtures/scopes/`conftest.py`/`pytestmark`, UTC timestamps, PDF internals, text encodings/UTF-8/BOM, regex (anchors, groups, lazy `+?`, lookbehind), closures, generators (`yield`, `yield from`, `Iterator`), slicing (`del lst[i:]`), chunking strategies, `pytest.mark.parametrize`, `PYTHONPATH` / how Python finds modules, dependency licenses, foreign keys / one-to-many / ON DELETE CASCADE, composite unique constraints as indexes, TRUNCATE with FKs, status state machines, idempotency, atomic commits, `BackgroundTasks` and its limits, `session.add_all`, fixing a test whose assumption became outdated (vs. weakening it), logging levels, producer/queue/consumer, workers as separate processes, retries + exponential backoff, job keys/dedupe, reconciliation sweeps, crash-only design, cron schedules, wrapping a third-party library behind your own interface, fakes vs. real integration tests, burst-mode workers, reading library source to find defaults (SAQ's 10s timeout), measuring before deciding, embeddings, vectors, cosine similarity / dot product / normalization, contrastive training (intuition), token limits and truncation, query vs passage prefixes, ONNX vs PyTorch, quantized models, a mini retrieval evaluation (top-1/top-3) and why labels matter, symlinks + content-addressed caches, why empty variables in `rm -rf` are dangerous (`set -euo pipefail`).
