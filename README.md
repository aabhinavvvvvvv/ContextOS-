# ContextOS

**Agentic RAG over live data and persistent memory.**

ContextOS is a personal knowledge and workflow assistant. It connects to your documents and GitHub, retrieves relevant evidence with hybrid (semantic + keyword) search, remembers past decisions, and runs multi-step workflows. Every answer cites its sources, and nothing consequential happens without your approval.

> Status: **Phase 3 complete** (persistent memory): documents are ingested, searchable (hybrid semantic + keyword) and answerable with cited, grounded answers; every question and answer is saved to a conversation, and follow-ups ("Why?") are understood from the recent turns. After each turn, the background worker extracts long-term memories (decisions, preferences, tasks, facts the user states), skipping ones it already knows and superseding ones that changed. Answers use the most relevant memories alongside the documents and cite them as `[M1]`. Time-bound memories ("on vacation until Friday") expire; memories can be corrected or deleted. Phase 4 complete: `/ask` is a LangGraph agent that plans tool steps for complex questions (search, list all open tasks), checks whether evidence is missing and searches once more if needed, degrades gracefully on failures, and returns a step timeline. See [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md).

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
    actions/         proposed actions, the approval flow (approve → execute exactly once), audit log
    auth.py          optional API token (Bearer) on every route except /health
    answers/         RAG: retrieve (hybrid search + relevant memories) → grounded prompt → LLM → verified citations;
                     graph.py: the LangGraph agent (rewrite → plan → act ⇄ check → generate / not_found);
                     planning.py: tool choice + evidence check (LLM, JSON); tools.py: search, list_tasks
    connectors/      sync GitHub repos (issues, PRs, commits; REST API, polling with `since`) and local folders into documents
    conversations/   every /ask turn (question + answer + cited sources), the raw record memory is built from
    memory/          long-term memory: the LLM extracts decisions, preferences, tasks and facts the user states (structured JSON output), stored with embedding and source message; `recall.py` finds the current memories closest to a question
    mcp_server.py    MCP server (stdio): search_knowledge, ask, list_open_tasks tools + contextos://memories resource
    llm.py           LLM client (Google Gemini via google-genai); the only module that knows the provider
    embeddings.py    local embedding model (fastembed, BAAI/bge-small-en-v1.5, 384 dims)
    jobs.py          Redis job queue (SAQ); the only place the API touches the queue library
    worker.py        background worker process: ingestion and memory-extraction jobs, plus once-a-minute sweeps that re-queue unfinished work
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

Uploads are saved immediately and ingested by the worker; after each `/ask`, the worker also extracts memories from the turn (needs `GEMINI_API_KEY`). If the worker is stopped, jobs wait in Redis. If a job never reached Redis, the worker's startup and once-a-minute sweep re-queue every document the database still marks `pending` or `processing`.

## Security

- **Human in the loop:** the agent can only *propose* actions; `POST /actions/{id}/approve` is the only thing that runs one, and a guarded state transition makes it run at most once.
- **Prompt injection:** the planner sees only the user's question (never retrieved documents), and the evidence check, which does see them, can only add lookups, not actions.
- **Permissions:** `ALLOWED_ACTIONS` limits which kinds can be proposed or approved. **Auth:** set `API_TOKEN` to require `Authorization: Bearer <token>` on every route except `/health`.
- **Audit:** every consequential event is recorded in `audit_log`.

## MCP server

ContextOS can be used from any MCP client (Claude Desktop, IDE assistants). The client starts it over stdio:

```json
{"mcpServers": {"contextos": {"command": "wsl", "args": ["-d", "Ubuntu-24.04", "--", "bash", "-lc",
  "cd ~/personal_projects/contextOS/backend && uv run python -m app.mcp_server"]}}}
```

Tools: `search_knowledge`, `ask`, `list_open_tasks`. Resource: `contextos://memories`. Read-only by design.

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness: the process is up |
| `GET` | `/health/ready` | Readiness: Postgres is reachable (503 if not) |
| `POST` | `/documents` | Upload a `.md`, `.txt` or `.pdf` file (multipart field `file`). 201, or 409 for duplicates, 413 too large, 415 unsupported type |
| `GET` | `/documents?limit=50` | List documents, newest first |
| `GET` | `/documents/{id}` | One document (404 if unknown). `status`: `pending` → `processing` → `ready` / `failed` (with `error`) |
| `GET` | `/documents/{id}/chunks` | The document's text chunks in reading order, with `heading` (Markdown) or `page_number` (PDF) |
| `POST` | `/ask` `{"question": "...", "top_k": 5, "conversation_id": null}` | Grounded answer: hybrid search plus the 3 current memories closest to the question, then the LLM answers **only** from those chunks and memories, citing them as `[n]` and `[Mn]` (if a memory and a document disagree, it says so). Returns `answer`, verified `citations` and `memory_citations`, token usage and `conversation_id` (send it back to continue the thread; 404 if unknown). In a conversation, a follow-up is first rewritten from the last 3 turns into a standalone question (`standalone_question`, e.g. "Why?" → "Why did we move ingestion to a Redis-backed worker?"), which is what gets searched and answered. Complex questions (several parts, or about tasks) are planned by the agent into up to 3 tool steps (`search`, `list_tasks`), each with its own part (`sub_questions`); an evidence check may add one more round; simple questions skip planning (no extra LLM call, at most 4 LLM calls otherwise). `timeline` lists every step with its duration and any error (failed planning/tools degrade instead of failing the request). If only some parts can be answered, the answer says which parts are missing. The turn is saved only if answering succeeds. 503 if no `GEMINI_API_KEY`, 429 if the free quota is used up, 502 on provider errors. |
| `GET` | `/conversations?limit=50` | Conversations, most recently active first |
| `GET` | `/conversations/{id}` | One conversation with its messages in order; assistant messages include the citations and memory citations shown at the time |
| `GET` | `/memories?limit=50&kind=decision&include_history=false` | Current long-term memories, newest first, with `source_message_id` and `conversation_id` (where each was learned). `kind`: `fact`, `decision`, `preference`, `task`. `include_history=true` also lists replaced memories (`superseded_by_id` / `superseded_at`) and expired ones (`expires_on` in the past). Extracted by the worker a few seconds after each `/ask` |
| `PATCH` | `/memories/{id}` `{"content": "..."}` | Correct a memory's wording (re-embedded). 404 if unknown, 422 if blank |
| `DELETE` | `/memories/{id}` | Forget a memory (204). If it had replaced an older memory, that one becomes current again. 404 if unknown |
| `GET` | `/actions?status=proposed` | Actions the agent proposed ("Add a task to…", "Open an issue for…"), newest first. Nothing runs until approved |
| `POST` | `/actions/{id}/approve` | Approve and run it now: `executed` with `result` (new task memory / GitHub issue URL) or `failed` with `error`. 409 if already decided, 403 if its kind isn't in `ALLOWED_ACTIONS` |
| `POST` | `/actions/{id}/reject` | Reject it; it can never run |
| `GET` | `/audit?limit=100` | Audit log, newest first: proposals, approvals, executions, memory edits/deletions, connector changes |
| `GET` | `/connectors` | Connected sources with `last_status` (`never`/`ok`/`error`), `last_error`, `last_synced_at` |
| `POST` | `/connectors` `{"kind": "github", "name": "owner/repo"}` or `{"kind": "folder", "name": "notes"}` | Connect a GitHub repository or a folder inside `FOLDER_ROOT` (201; 400 invalid or outside the root; 409 already connected). The first sync is queued at once; the worker re-syncs every 15 minutes. Synced items become documents (`source`, `source_url`), and citations link to them |
| `POST` | `/connectors/{id}/sync` | Sync now (202) |
| `DELETE` | `/connectors/{id}` | Disconnect and delete everything it synced (204) |
| `GET` | `/search?q=...&limit=5&mode=hybrid` | Search chunks, best first, with citation fields. `mode=hybrid` (default): semantic + keyword merged by Reciprocal Rank Fusion, with each method's rank. `mode=semantic`: closest in meaning (score = cosine similarity). `mode=keyword`: Postgres full-text search with stemming, `"phrases"`, `or`, `-exclude` (score = ts_rank). Scores are only comparable within one mode. |

```bash
curl -F "file=@notes.md" http://localhost:8000/documents
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundations and project setup | ✅ |
| 1 | FastAPI backend, document ingestion, PostgreSQL, background worker | ✅ |
| 2 | Embeddings, hybrid retrieval, grounded answers with citations | ✅ |
| 3 | Persistent memory | ✅ |
| 4 | Agentic workflows with LangGraph | ✅ |
| 5 | Live connectors (GitHub, local docs) and MCP | |
| 6 | Controlled actions with human approval | |
| 7 | Frontend | |
| 8 | Evaluation, testing and observability | |
| 9 | Deployment | |
