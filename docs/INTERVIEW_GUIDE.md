# ContextOS: Interview Guide

How ContextOS was built, the problems hit along the way and how each was solved. Kept up to
date phase by phase. Every problem listed here really happened (dates are in
`PROJECT_PROGRESS.md`); nothing is invented for the story.

---

## 1. The pitch

**30 seconds.** ContextOS is a personal knowledge and workflow assistant: agentic
RAG over live data and persistent memory. It ingests documents and GitHub data, answers
questions with hybrid (semantic + keyword) retrieval and verified citations, remembers what
the user tells it (decisions, preferences, tasks) across conversations, splits complex
questions into steps with a LangGraph agent, and proposes actions that only run after the
user approves them.

**2 minutes.** Walk through one request:
"What did we decide about the API architecture, what tasks are unfinished, and prepare a
prioritized plan for today."
1. If it's a follow-up, it is rewritten into a standalone question using the last 3 turns.
2. A cheap regex sees several parts, so one LLM call plans it into tool steps:
   `search("API architecture decision")` and `list_tasks()`. "Prepare a plan" isn't a lookup:
   it happens when answering.
3. Each step runs: hybrid search (pgvector HNSW + Postgres full-text, fused with Reciprocal
   Rank Fusion) plus recall of the closest current memories; `list_tasks` returns every
   open task memory.
4. An evidence check decides whether to search again (bounded by a step limit).
5. Gemini answers only from the numbered sources `[1]` and memories `[M1]`; citations are
   verified against what was actually shown.
6. The turn is saved. A background worker then extracts new memories from what the user
   said, skipping known ones and superseding changed ones.

---

## 2. Architecture

```
React + TypeScript + Tailwind  (Vite dev server / Caddy in production)
        │  HTTP/JSON
        ▼
FastAPI ── /ask ──► LangGraph agent: rewrite → plan → act (tools) → check ⟲ → generate
   │                         │
   │                         └─► Gemini (google-genai, behind app/llm.py)
   ├──► PostgreSQL + pgvector: documents, chunks (+HNSW, +tsvector/GIN), conversations,
   │                            messages, memories, connectors, actions, audit log, llm_calls
   └──► Redis ◄── SAQ worker: ingestion, memory extraction, connector sync, sweeps
```

| Choice | Why | Alternative considered |
|---|---|---|
| FastAPI + Pydantic | async, typed request/response models, OpenAPI docs for free | Flask, Django |
| Postgres + pgvector | one datastore: vectors, full-text, relational data and transactions together | separate vector DB (Pinecone, Qdrant) |
| Hybrid search + RRF | embeddings miss rare exact terms, keywords miss paraphrases; RRF merges ranks, not incomparable scores | semantic only |
| fastembed bge-small (384d, ONNX) | local, free, CPU-only; picked by measuring 5 models on 10 real questions | paid embedding APIs |
| Gemini free tier | free; the provider is isolated in one file (`llm.py`) | OpenAI / Anthropic (paid) |
| SAQ on Redis | durable background jobs with retries, dedupe keys, cron | FastAPI BackgroundTasks (lost on restart), Celery (heavier) |
| LangGraph | explicit graph, state reducers, step streaming, interrupts for human approval | plain while loop, LangChain AgentExecutor |

---

## 3. Phase by phase

### Phase 0: Foundations
- **Built:** repo, `.gitignore`, `.env.example`, Docker Compose (Postgres 17 + pgvector, Redis 7.4 with health checks and named volumes), uv-managed backend.
- **Decisions:** host ports 5433/6380 to avoid clashing with other local databases; secrets only in `.env` (gitignored), with a pre-push scan of git history for every `.env` value.
- **Problems → fixes:**
  - Docker didn't work inside WSL → Docker Desktop's WSL integration was off for the distro; enabled it.
  - pnpm was broken on the machine → used npm.

### Phase 1: Backend and ingestion
- **Built:**
  - FastAPI app with liveness (`/health`) and readiness (`/health/ready`, 503 when the DB is down)
  - typed settings
  - async SQLAlchemy and Alembic migrations
  - document upload with content-addressed storage (SHA-256 file names, 409 on duplicates)
  - text extraction (md/txt/pdf) and structure-aware chunking (1500 chars, 200 overlap, split by headings, then paragraph, sentence and word)
  - durable background ingestion with SAQ
- **Problems → fixes:**
  - Markdown `#` lines inside code fences were treated as headings → the extractor tracks fence state.
  - PDFs contained NUL characters that Postgres text columns reject → normalized during extraction.
  - **FastAPI `BackgroundTasks` lose work on restart:** a document sat in `pending` for a day → moved ingestion to a Redis queue (SAQ) plus a worker **sweep** (on startup and every minute) that re-queues anything still `pending`/`processing`. Live demo: the stuck README was rescued.
  - SAQ's job timeout silently defaulted to 10 s (found by **reading the library source**) → set explicitly (300 s for ingestion).
  - A test relied on `expire_on_commit=False` by accident → service functions read the values they need before `commit()`.

### Phase 2: Embeddings and RAG
- **Built:**
  - local embeddings
  - `chunks.embedding vector(384)` with an HNSW index
  - keyword search with a generated `tsvector` column plus a GIN index
  - hybrid search with RRF (k=60)
  - grounded answers with numbered `<source>` blocks and verified `[n]` citations
- **Problems → fixes:**
  - **HNSW silently returned at most 40 rows** (pgvector's `ef_search` default caps the candidate list), so hybrid search over-fetching got fewer candidates than asked → found by a failing test, fixed with `SET LOCAL hnsw.ef_search = max(limit, 40)` per query.
  - Alembic autogenerate didn't import `pgvector.sqlalchemy` in migrations → fixed the migration template (`script.py.mako`).
  - Adding a NOT NULL embedding column to a table with existing rows → a data migration deleted the derived chunks and reset documents to `pending`, so the worker re-ingested them (derived data can be rebuilt; source data can't).
  - **The model skipped the citation on single-source answers** → added a short final reminder at the very end of the prompt (instructions nearest to where generation starts are followed most reliably). Measured before/after: 4/4 → 5/5 cited, over-refusals 1 → 0.
  - **"Event loop is closed"** in live tests → the cached async client belonged to the first `asyncio.run` loop; each test now creates a fresh client.
  - Embedding columns slowed every ORM load → `deferred` + `raiseload`, so they are loaded only when explicitly selected.

### Phase 3: Persistent memory
- **Built:**
  - conversations and messages (citations stored as a JSONB snapshot)
  - follow-up rewriting (working memory)
  - background extraction of semantic memories (decision / preference / task / fact) with provenance
  - dedup and supersession
  - memories used in answers (`[M1]`)
  - expiry of time-bound memories
  - edit and delete
- **Key ideas:**
  - **History vs. memory:** history is the raw transcript, memory is distilled, durable statements.
  - **Supersede, don't delete:** "keep Gemini" → "switched to Claude" keeps both, with `superseded_by_id`, so "what did we believe before?" stays answerable.
  - **Memories only from what the user says**, never from the assistant's answer (that comes from documents, which are already searchable).
  - **Structured output + Pydantic validation:** the JSON schema makes valid output likely; validation makes bad output harmless.
  - **Idempotent jobs:** `messages.memories_extracted_at` is set in the same transaction as the inserts, so a job that runs twice stores nothing twice.
- **Problems → fixes:**
  - **Race on message `position`:** two simultaneous turns picked the same slot → `SELECT … FOR UPDATE` on the conversation row plus a unique `(conversation_id, position)` constraint. Proven: without the lock the concurrency test failed 3/3.
  - Ordering by `created_at` was wrong: Postgres `now()` is the same for the whole transaction, so question and answer had the same timestamp → explicit `position` column.
  - **`MissingGreenlet`:** reading `conversation.id` after commit triggered a hidden reload, which async SQLAlchemy forbids → build ids before committing.
  - Two extraction jobs for the same turn racing → lock the message row and re-check before saving. Proven with a slow fake LLM: without the re-check, memories were stored twice.
  - **Poison message:** a request the provider always rejects would be retried forever by the sweep → `LLMRequestError` (4xx other than 429) counts as "nothing to remember".
  - A backlog sweep could burn the per-minute quota → batch of 5 per minute.
  - The model ignored "one fact per memory" and stored a finished task as a task → added an example deliberately **different** from the eval case (to avoid overfitting) plus a "finished task becomes a fact" rule. Eval 7/8 → 8/8.
  - **Circular import** (answers → memory.service → extraction → answers) → moved the read-only lookup into `memory/recall.py`.
  - Live tests failed twice because **my assertions guessed the wording** (expected exactly one memory; banned the "couldn't find" sentence anywhere) → assert the requirement, not the phrasing.
  - Back-to-back live evals hit the free tier's per-minute limit → space live runs about 1 minute apart.

### Phase 4: Agentic workflows
- **4.1:** rebuilt `/ask` as a LangGraph graph with **no behaviour change**, proven by all 132 existing tests passing unmodified. Per-request dependencies (DB session, LLM) go in the LangGraph **runtime context**, not the state (the state is data about the question and may be checkpointed). A mutation test showed the token-count reducer matters: without `operator.add`, the totals were (100, 10) instead of (200, 20).
- **4.2:** query decomposition. A cheap regex decides whether to plan, so single questions cost no extra call. One JSON-schema call returns up to 3 sub-questions; unusable output falls back to the original question. Retrieval is sequential (one DB session can't run queries in parallel, so no LangGraph `Send`). Answers may be partial and name the missing parts. Live: decomposition eval 5/5.
- **4.3–4.6, the agent:** `rewrite → plan → act ⇄ check → generate`.
  - **Tools** are a registry (`search`, `list_tasks`). The planner only *names* a tool; one `Literal` type feeds both the JSON schema and the prompt, and an unknown tool name fails validation, so it can never run.
  - **Loop:** the evidence check may add up to 2 lookups, once (`MAX_ROUNDS = 2`), so there are at most 4 LLM calls per question. LangGraph's `recursion_limit` is the last-resort guard.
  - **Failure handling:** planning/check/rewrite errors degrade to the simple path; a failing tool is rolled back and skipped; only the final answer call can fail the request.
  - **Timeline:** every node appends `{step, summary, duration_ms, error}` through a list reducer, and `/ask` returns it.
  - **Results:** live planning/tool eval 5/5. A real run took about 4.9 s for 3 LLM calls (vs. about 1.5 s on the fast path): agentic RAG trades latency and quota for coverage, which is why simple questions skip it.
- **Problems → fixes:**
  - Why `list_tasks` exists: "what is unfinished?" via semantic recall returned only the 3 most *similar* memories, not all open tasks. Some questions need a different access path (a filter), not better similarity.
  - Bug caught in review: de-duplicating with a reversed-dict trick kept the right values but put the keys in last-seen order, which would have scrambled the evidence order. Replaced with an explicit `setdefault` loop.
  - A failed SQL statement poisons the session's transaction, so the next tool would fail too → `rollback()` after a tool error.
  - A script run from `/tmp` couldn't `import app` (Python puts the *script's* directory on the import path) → `PYTHONPATH=.`.

### Phase 5: Live data connectors and MCP
- **Built:** GitHub connector (issues, PRs, commits) and folder connector, synced into ordinary documents, so hybrid search, citations and the agent work on them with no new retrieval code. Also an MCP server exposing ContextOS to other AI apps.
- **Key decisions:**
  - **Polling with `since`, not webhooks:** webhooks need a public URL (impossible on a laptop) plus signature checks; polling every 15 minutes with `since=last sync` costs about 2 requests per repo.
  - **Stable `source_key` per item** (`github:owner/repo#12`): a changed item *replaces* its document (old chunks cascade away); an unchanged one is skipped by SHA-256. The same "stale data" idea as memory supersession.
  - **`since` = the sync's start time**, not its end, so items updated *during* a sync are fetched again next time instead of being missed.
  - **Folder sandbox:** only paths inside `FOLDER_ROOT`, checked after `resolve()` (which follows `..` and symlinks), so the API can't read `/etc/passwd`.
  - **Sync errors are recorded, not raised:** `last_status`/`last_error` on the connector; the schedule retries. A rate limit is surfaced with its reset time.
  - **MCP server is read-only:** writes go through Phase 6's approval flow. MCP basics: client ↔ server over stdio/HTTP; *tools* are called by the model, *resources* are read by the application.
- **Problems → fixes:**
  - **MCP SDK 2.x renamed `FastMCP` to `MCPServer`:** caught at import; inspected the installed API instead of trusting memory (same lesson as google-genai 3.0).
  - Alembic autogenerate missed a new CHECK constraint on an existing table → added by hand; verified with downgrade/upgrade and `alembic check`.
  - Testing an external API without the network → injectable HTTP transport (`httpx2.MockTransport`) and a fake GitHub that records requests (also proves `since` is sent on the second sync).
  - Inline shell variables came out empty again (`$f` in `wsl bash -c '...'`) → scripts for anything with variables.
- **Result:** a real sync of the project's own public repo: 30 commits → 30 searchable documents.

---

## 4. Problems we faced: quick reference

| # | Symptom | Root cause | Fix | Lesson |
|---|---|---|---|---|
| 1 | Document stuck in `pending` for a day | `BackgroundTasks` die with the process | Redis job queue + reconciliation sweep | Background work must be durable *and* reconciled |
| 2 | Hybrid search returned fewer rows than asked | HNSW `ef_search` = 40 caps index-scan results | `SET LOCAL hnsw.ef_search` per query | Approximate indexes have hidden limits; test result counts |
| 3 | Duplicate `position` under concurrency | check-then-insert race | Row lock + unique constraint | Prove races with a test, then lock |
| 4 | `MissingGreenlet` | lazy reload after commit in async SQLAlchemy | Read values before commit | Async ORMs forbid hidden I/O |
| 5 | Memories stored twice | two job runs overlapping | Lock + re-check at save time | At-least-once delivery needs idempotent consumers |
| 6 | Single-source answers without citation | instruction too far from generation | Final reminder at the end of the prompt | Measure prompt changes before/after |
| 7 | "Event loop is closed" | async client bound to a closed loop | Fresh client per `asyncio.run` | Async resources belong to one event loop |
| 8 | Live evals failing in bursts | free-tier per-minute quota | Space runs; batch-limit background jobs | Cost/quota is a design constraint for agents |
| 9 | Circular imports | two modules needing each other | Split out a small module | Dependency direction matters |
| 10 | Secret-scan false alarm; empty `rm -rf` variable | shell quoting across Windows → WSL produced empty variables | Scripts with `set -euo pipefail`, never inline variables in destructive commands | Tooling bugs can be dangerous; fail fast |
| 11 | Tests failing on correct model output | assertions guessed wording | Assert requirements | Evals test behaviour, not phrasing |

---

## 5. Trade-offs I can defend
- **No similarity cutoff for memories in answers:** top 3, and the model ignores irrelevant ones (verified live). A cutoff needs per-model tuning.
- **Dedup inside the extraction call** (existing memories shown numbered, plus `replaces`) instead of a second classification call: half the LLM calls; may miss a related memory that isn't in the top 5.
- **Exact scan on the memories table** (no HNSW index): a personal memory table stays small; exact search never misses.
- **Expiry checked at read time** (`expires_on >= current_date`), with no cleanup job.
- **Keyword-graded live evals:** cheap and deterministic, but they check behaviour coarsely; Phase 8 adds a benchmark.
- **Simplicity over production hardening:** it's a personal project; production concerns are documented in one line rather than built.

---

## 6. Likely interview questions (short answers)
- **Why hybrid search?** Embeddings capture meaning but miss rare exact terms (ids, names); full-text gets exact terms but misses paraphrases. RRF merges by rank, because the two scores aren't comparable.
- **How do you stop hallucination?** Answers come only from numbered sources and memories, citations are verified against what was shown, there's an explicit "couldn't find" reply, and nothing is answered when retrieval is empty.
- **What makes it agentic?** The LLM chooses the steps (which tools, whether to search again) within a step limit; simple questions skip the agent entirely.
- **How does memory stay correct over time?** Supersede on change, expire time-bound facts, the user can edit and delete, and provenance links every memory to the message it came from.
- **How do you evaluate an LLM system?** Unit tests with a fake LLM for plumbing; small live evals with keyword checks for behaviour; a 50+ scenario benchmark for retrieval, tool choice, citations and memory (Phase 8).
- **What would you change for production?** See the per-phase "production note" lines in `PROJECT_PROGRESS.md`: auth/multi-user, managed Postgres, monitoring and alerting, rate limiting, secrets management.

---

## 7. Checklist: can I explain…
- [ ] The full `/ask` data flow, node by node
- [ ] Chunking strategy and why 1500/200
- [ ] Embeddings, cosine similarity, HNSW and its `ef_search` limit
- [ ] tsvector/tsquery, GIN, stemming
- [ ] RRF and why ranks rather than scores
- [ ] Grounding, citation verification, prompt injection defences
- [ ] Durable jobs: queue, retries, idempotency, sweeps, poison messages
- [ ] Row locks vs. unique constraints; the position race
- [ ] Working vs. episodic vs. semantic memory; supersession; expiry
- [ ] LangGraph state, nodes, conditional edges, reducers, runtime context
- [ ] Query decomposition and the cheap pre-check
