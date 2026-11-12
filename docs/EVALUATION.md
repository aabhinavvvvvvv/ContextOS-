# Evaluation

How ContextOS is evaluated, beyond "the answers look right".

## Three layers

| Layer | What it checks | Cost | Where |
|---|---|---|---|
| Unit + integration tests | Plumbing: every endpoint, the agent graph, idempotency, races, failure handling. The LLM is a scripted fake, so tests are deterministic and free | free, ~35 s | `backend/tests/` (`uv run pytest`) |
| Live behaviour evals | Small, targeted checks of one prompt each (rewrite, extraction, dedup, planning, answers) against the real model | ~40 Gemini calls | `uv run pytest -m llm` |
| Benchmark | 60 scenarios end to end over a fixed corpus | offline: free; live: ~60 Gemini calls | `uv run python -m evals.run [--live]` |

## The benchmark

A fictional project, **Atlas** (a delivery-tracking product): 10 documents (`backend/evals/corpus/`: ADRs, meeting notes, runbook, incident review, onboarding, roadmap, team, pricing) and 8 seed memories (`memories.json`). The runner rebuilds its own database (`contextos_eval`) through the real ingestion pipeline and embedding model on every run, so results are reproducible and the dev database is never touched.

Scenarios (`scenarios.jsonl`) and their metrics:

| Category | # | Metric |
|---|---|---|
| retrieval | 25 | **hit@1 / hit@5**: is a chunk of the expected document containing the expected fact ranked 1st / in the top 5? **MRR**: mean of 1/rank |
| memory | 8 | **hit@3**: is the expected memory among the 3 recalled for the question? |
| planning | 12 | **correct tools**: the required tools are chosen, the step limit is respected, and **no action is proposed unless requested** |
| answer | 15 | **correct**: required facts present (keyword groups) and not refused; **refusals**: unanswerable questions get the "couldn't find" reply; **citations present**; **citation support**: a cited source actually contains the expected fact |

Deliberate traps: the meeting notes say the mobile beta starts on December 1, while a newer memory says it moved to December 8 (conflict resolution); "What is our remote-work policy?" and "What is the capital of France?" have no answer in the data (refusal); "What issues did the incident review raise?" must stay a lookup, not an issue proposal.

A test (`tests/test_eval_dataset.py`) checks that the dataset itself is consistent: every expected phrase really exists in the named document or memory, and every tool name is real. A typo in a scenario would otherwise silently make it unpassable.

## Results (2026-11-13, gemini-3.5-flash-lite, bge-small-en-v1.5)

```
retrieval   hit@1 21/25 (84%)   hit@5 25/25 (100%)   MRR 0.908
memory      hit@3 8/8 (100%)
planning    correct tools 12/12 (100%)
answers     correct 13/13 (100%)   refusals 2/2 (100%)
citations   present 13/13 (100%)   supported 13/13 (100%)
latency     p50 1.4 s   p95 4.1 s   tokens/answer 1053
```

Conflict example (a05): *"The mobile app beta starts on December 8, 2026, as the team moved it from the original December 1 date due to the login timeout bug [M1][1]."* The newer memory leads; the older document is still cited.

### What the misses show
- r01, r03, r09: the right **document** ranked first, but its *Decision* chunk outranked the *Reasons* chunk, because heading-based chunking splits them. The answer still sees both (top 5), so answers were unaffected; document-level hit@1 is 24/25.
- r19 ("What is planned for Q4?"): meeting notes mentioning the "Q4 release" outranked the roadmap's Q4 section (rank 5). Short, generic queries favour any chunk repeating the keyword: the case a cross-encoder **reranker** would fix.

### Limitations (read before quoting the numbers)
- **Small and self-written.** 60 scenarios written by the people who built the system can't reveal blind spots they share. 100% here means "handles these cases", not "has no failure modes".
- **Keyword grading** is cheap and deterministic but coarse: a correct answer phrased differently can fail, and a wrong answer containing the keyword can pass. An LLM-as-judge or human review would be the next step.
- **Single run** of a nondeterministic model (temperature 0.2). Earlier live evals occasionally flipped on reruns (see `PROJECT_PROGRESS.md`).
- The corpus is tiny (≈25 chunks); ranking gets harder with thousands of chunks from similar documents.

## Observability

Every LLM call (purpose, model, tokens, latency, error) and every HTTP request (route template, status, latency) is recorded; `GET /metrics?days=7` and the Metrics page summarise calls, error rates, p50/p95 latency, tokens and estimated cost. Cost uses `LLM_INPUT_USD_PER_MILLION` / `LLM_OUTPUT_USD_PER_MILLION` (example paid-tier prices; the free tier is $0). Recording never breaks a request: a failed metrics write is logged and dropped.
