# Phase 2: Correctness Fixes - Research

**Researched:** 2026-04-08
**Status:** Ready for planning

## Scope Anchors

- `BUG-01`: `app/ingestion/embedder.py` leaks `AsyncOpenAI` resources because the client is instantiated once per function call without an async context manager.
- `BUG-02`: `app/core/status.py` finalizes sessions with a non-locking `db.get()` plus `db.refresh()` flow, leaving a race window where cancellation can be overwritten.
- `PERF-01`: the embedder fallback path is sequential today; it should move to bounded parallelism with `asyncio.gather(return_exceptions=True)` and `asyncio.Semaphore(10)` without changing partial-failure behavior.

## Current Code Surface

### Session finalization and cancellation

- `app/core/status.py` currently calls `await db.get(Session, session_id)` and then `await db.refresh(result)` before checking `SessionStatus.CANCELLED`.
- `app/api/routes/sessions.py` `cancel_pipeline()` currently calls `await db.get(Session, session_id)` and writes `SessionStatus.CANCELLED` without acquiring a row lock.
- The repo-level state contract in `AGENTS.md` is strict: `POST /sessions/{id}/run` writes `GENERATING` once, `core.status.finalise_session()` owns terminal writes, and in-progress statuses come from `status_projection()`. Phase 2 must preserve that ownership model.
- Existing tests already exercise `finalise_session()` behavior indirectly in `tests/test_phase3.py`, `tests/test_m008_integration.py`, and `tests/test_m017_integration.py`, but there is no focused race/locking regression suite yet.

### Embedding ingestion

- `app/ingestion/embedder.py` instantiates `AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0)` directly and reuses it for the batch and fallback loops.
- The batch path uses `openai_client.embeddings.create(model="text-embedding-3-small", input=texts)`.
- The fallback path loops sequentially over texts, catches per-chunk exceptions, logs warnings, and appends `None` to preserve partial success behavior.
- Pinecone upserts occur after embedding generation via `asyncio.to_thread(index.upsert, ...)`, then the DB commit persists the `CookbookChunk` rows.

## Constraints That Matter For Planning

### Locking fix constraints

- The first and only DB read inside `finalise_session()` must be a locking `select(Session)...with_for_update()` query. Mixing `db.get()` with `with_for_update()` risks returning stale identity-map state and silently preserving the race.
- The cancellation route must take the same lock so the write ordering between `finalise_session()` and `cancel_pipeline()` becomes deterministic.
- The lock scope must stay narrow: read locked row, evaluate terminal/cancel guard, write terminal state, commit. `status_projection()` should remain checkpoint-based and must not start reading the locked `sessions` row for in-progress status.

### Embedder fix constraints

- `AsyncOpenAI` must be wrapped once at function entry with `async with AsyncOpenAI(...) as openai_client:`. Opening clients per chunk or per fallback coroutine would make the hot path slower and churn sockets.
- The parallel fallback must keep the same partial-failure semantics as today: failed chunks are logged and skipped, successful chunks still upsert, and the whole book ingestion does not abort because one chunk failed.
- `asyncio.gather()` must use `return_exceptions=True`, and the semaphore must be acquired via `async with sem:` so failed tasks cannot strand semaphore slots.
- The same `openai_client` instance should be shared across fallback coroutines; no nested client factories in the loop.

## Recommended Phase Split

### Wave 1 - lock terminal status ownership

- Update `app/core/status.py` to use a single locking read in `finalise_session()`.
- Update `app/api/routes/sessions.py` `cancel_pipeline()` to acquire the same row lock before writing `CANCELLED`.
- Add targeted regression coverage proving the `CANCELLED` guard and terminal-state no-op behavior survive the refactor.

### Wave 2 - fix embedder lifecycle and fallback concurrency

- Update `app/ingestion/embedder.py` to scope `AsyncOpenAI` with a context manager and replace the sequential fallback loop with bounded concurrent single-chunk requests.
- Add targeted embedder tests proving one-client-per-call behavior and partial-failure isolation.
- Run the full non-integration suite after the embedder changes so Phase 2 closes with the repo-wide regression gate green.

## Critical Pitfalls

1. **Identity-map stale lock in `finalise_session()`**
   - Do not call `db.get()` anywhere in the function once the locking refactor lands.
   - Prefer `select(Session).where(Session.session_id == session_id).with_for_update()` as the sole read path.

2. **Locking only one side of the race**
   - Updating `finalise_session()` without updating `cancel_pipeline()` still allows write-order anomalies because the cancel route can write `CANCELLED` without waiting on the finalizer.

3. **Per-chunk client construction**
   - Any `AsyncOpenAI(` call inside the fallback loop is incorrect and would turn the fix into a throughput regression.

4. **`asyncio.gather` first-exception behavior**
   - If `return_exceptions=True` is omitted, one failed chunk can cause sibling coroutines to keep running without controlled result handling.

## Validation Architecture

### Quick checks

- `pytest -q tests/test_status_finalisation.py tests/test_api_routes.py -o addopts='' -k 'finalise or cancel'`
- `pytest -q tests/test_embedder.py -o addopts=''`

### Full gate

- `pytest -m "not integration" -v`

### Sampling guidance

- Run the targeted status/cancel command after each task in Wave 1.
- Run the targeted embedder command after each task in Wave 2.
- Run the full non-integration suite after Wave 2 before phase verification.

## Planning Implications

- The plans should avoid touching frontend code, graph topology, or unrelated scheduler logic.
- Phase 2 can stay in two execute plans because the status-locking work and embedder work touch different production files and can be verified with separate targeted suites.
- The final full-suite regression check should happen only after both plans are complete, so the embedder plan should carry the phase-wide green-suite gate.
- Security enforcement is enabled in `.planning/config.json`, so each plan must include a `<threat_model>` block even though this phase is correctness-focused.
