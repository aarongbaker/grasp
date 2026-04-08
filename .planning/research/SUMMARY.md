# Project Research Summary

**Project:** grasp — backend hardening milestone
**Domain:** Python async API hardening (FastAPI + Celery + LangGraph + SQLAlchemy)
**Researched:** 2026-04-08
**Confidence:** HIGH

## Executive Summary

This is a production hardening pass on a fully functional backend — not a greenfield build. The system (FastAPI 0.111, SQLAlchemy 2.x via SQLModel 0.0.19, Celery 5.4, slowapi 0.1.9, asyncpg 0.29, LangGraph) already runs end-to-end with 99 passing tests. Research identified five specific correctness and security gaps to close: an AsyncOpenAI connection leak in the embedding worker, a read-modify-write race in session finalization, missing bounds validation on kitchen config, absent rate limiting on the session creation endpoint, and zero test coverage for admin routes and Celery task failure paths. All gaps have clear, low-risk fixes using libraries already installed — no new dependencies are required.

The recommended approach follows a strict build order: add missing tests first (additive, zero behavioral risk), then apply the two correctness fixes (SELECT FOR UPDATE and AsyncOpenAI context manager), then close the security surface (bounds validators, rate limiting, RAG user assertion). The performance work (embedding parallelization, RAG context caching, interval-based scheduler optimization) is independent and lower urgency — the scheduler's _IntervalIndex already handles the primary O(n²) case, and profiling may reveal the optimization is already done. Anti-features to avoid entirely: LangGraph checkpoint migration, S3 object storage migration, full Celery task revocation, enricher threshold configuration, and connection pool pre-tuning — all add complexity without addressing any confirmed production incident.

The primary risk in this milestone is in the SELECT FOR UPDATE fix: SQLAlchemy's identity map can return a stale cached object even after a locking query is issued, silently leaving the race condition intact. The fix must use a single select(...).with_for_update() as the first and only DB read in finalise_session() — never mix db.get() with with_for_update(). The rate limiting fix has a secondary risk: the sessions router currently creates a disconnected Limiter instance that bypasses Redis-backed storage; the fix requires a shared limiter module (app/core/limiter.py) to avoid circular imports and ensure all rate limit checks use the same backend.

## Key Findings

### Recommended Stack

The base stack requires no changes. All five hardening gaps are closed using already-installed libraries: slowapi 0.1.9 for rate limiting, asyncio stdlib for embedding parallelization, AsyncOpenAI context manager API for the connection leak, SQLAlchemy 2.x with_for_update() for the race fix, and httpx.ASGITransport + pytest-asyncio for new test coverage. The codebase already uses the correct modern patterns in its existing tests (ASGITransport, not deprecated app= shorthand; asyncio_mode = auto; session-scoped fixtures).

**Core technologies — verified patterns:**
- `httpx 0.27` + `pytest-asyncio 0.23`: `ASGITransport(app=app)` with `AsyncClient` — already correct in codebase
- `slowapi 0.1.9`: `@limiter.limit("5/minute")` with shared limiter instance; requires `request: Request` parameter on decorated routes
- `SQLAlchemy 2.x` (via SQLModel): `select(Model).where(...).with_for_update()` — `AsyncSession.get()` does not support locking
- `AsyncOpenAI` SDK: `async with AsyncOpenAI(...) as client:` wraps entire function body, not individual calls
- `Celery 5.4`: `task_always_eager` is removed — test `_run_pipeline_async()` directly as an async function

### Expected Features

**Must have (table stakes) — correctness/security gaps, production is unsafe without them:**
- **AsyncOpenAI context manager** — one-function fix, prevents connection accumulation in long-lived Celery workers
- **SELECT FOR UPDATE in `finalise_session()`** — closes real TOCTOU race; requires single-query approach, not `db.get()` + `with_for_update()`
- **Pydantic bounds validators on kitchen config** — prevents scheduler abuse via unconstrained `max_burners`/equipment inputs
- **Rate limiting on `POST /sessions`** — slowapi already installed; add shared limiter module + decorator; must be per-user (JWT sub), not per-IP
- **Admin + health route tests** — production code paths with zero CI coverage; broken invite route silently fails onboarding
- **Celery task failure tests** — task silencing keeps sessions in GENERATING forever; must test `_run_pipeline_async` error paths directly

**Should have (differentiators) — improves reliability, production is not immediately unsafe:**
- **RAG chunk user_id assertion** — defense-in-depth against Pinecone metadata bypass; check metadata dict, not DB
- **Embedding fallback parallelization** — sequential fallback is 15s for 50 chunks; `asyncio.gather` + `Semaphore(10)` reduces to ~1.5s
- **Equipment CRUD + dag_merger interval tests** — closes data corruption risk on equipment delete
- **Kitchen edge case tests** — prerequisite for safe scheduler optimization
- **RAG context cache** — eliminates N+1 Pinecone queries per pipeline run (5 round trips to 1)
- **Interval-based scheduler optimization** — verify O(n²) still exists before investing; `_IntervalIndex` may already handle it

**Defer (out of scope for this milestone):**
- LangGraph checkpoint migration — no production incidents; migration has no rollback path
- Base64 PDF to object storage — scaling fix for load levels not yet reached
- Session cancellation with Celery task revocation — SELECT FOR UPDATE fix already closes the double-write risk
- PgBouncer / connection pool tuning — no evidence of pool exhaustion
- Dietary restriction enforcement — explicitly out of scope in PROJECT.md

### Architecture Approach

The hardening work divides into four clean component groups with explicit integration boundaries. No cross-cutting structural changes are needed. The build order is determined by two rules: additive-only changes (new tests) come before behavioral changes (bug fixes), and bug fixes with test coverage come before performance optimizations that require a test regression gate. The MockDBSession class in test_api_routes.py should be extracted to tests/helpers.py as the first action — it is a shared dependency for admin, health, and task tests.

**Major components and their hardening work:**
1. **`app/core/limiter.py` (new)** — shared slowapi limiter instance; eliminates disconnected per-router Limiter objects
2. **`app/core/status.py`** — replace `db.get()` + `db.refresh()` with single `select(...).with_for_update()`; hold lock through `db.commit()`
3. **`app/ingestion/embedder.py`** — wrap `AsyncOpenAI` at function entry; parallelize fallback loop with `asyncio.gather(return_exceptions=True)` + `Semaphore(10)`
4. **`tests/helpers.py` (new)** — extracted `MockDBSession` shared by `test_admin_health.py` and `test_tasks_unit.py`
5. **`tests/test_admin_health.py` (new)** — admin invite CRUD + health check coverage using `ASGITransport` + `dependency_overrides`
6. **`tests/test_tasks_unit.py` (new)** — `_run_pipeline_async` success/failure paths with patched engine, checkpointer, and graph

### Critical Pitfalls

1. **Identity map cache defeats SELECT FOR UPDATE** — if `db.get(Session, id)` runs anywhere in `finalise_session()` before the `with_for_update()` query, SQLAlchemy returns the cached in-memory object and the cancellation check reads stale data. The lock is acquired but the guard is bypassed. Fix: make the `select(...).with_for_update()` the only DB read; add `execution_options(populate_existing=True)` if in doubt.

2. **Disconnected limiter instances bypass Redis backend** — the sessions router creates its own `Limiter()` at import time, disconnected from `app.state.limiter`. Rate limit hits never reach the registered exception handler. Fix: `app/core/limiter.py` singleton imported by both `main.py` and `sessions.py`; `request: Request` is mandatory on all decorated routes.

3. **slowapi in-memory state bleeds between tests** — `MemoryStorage` is module-level; exhausting the rate limit in one test causes 429s in the next. Fix: reset `limiter._storage` in an `autouse` fixture, or create a fresh `Limiter` instance per test; never mix rate-limit correctness tests and functional flow tests in the same limiter state.

4. **AsyncOpenAI context manager opened per-chunk causes connection churn** — wrapping `AsyncOpenAI` inside the fallback loop creates one TCP connection per chunk (10-50x slower than sequential). Fix: one `async with AsyncOpenAI(...) as client:` at function entry; pass the same client instance to all `asyncio.gather` coroutines.

5. **`asyncio.gather` swallows exceptions and leaks coroutines** — default `gather` raises on first exception but leaves siblings running unconsumed. Fix: always use `return_exceptions=True` and post-filter results; always use `async with sem:` so cancellation cannot leave semaphore slots permanently decremented.

## Implications for Roadmap

Based on research, the hardening milestone maps naturally to three phases ordered by risk profile: additive-only → narrow behavior fixes → security surface closure. Performance work is a fourth optional phase gated on profiling confirmation.

### Phase 1: Test Infrastructure
**Rationale:** All test additions are additive — they cannot regress existing behavior. Running them first discovers bugs in existing code before the bug-fix phase changes anything. The MockDBSession extraction is a prerequisite shared by admin and task test files.
**Delivers:** Full CI coverage of admin routes, health endpoint, and Celery task failure paths; shared test helper extracted; 99 to 130+ passing tests
**Addresses:** Admin + health route tests, Celery task failure tests (table stakes)
**Avoids:** Rate limit test state isolation pitfall — autouse reset fixture pattern established before rate limiting lands

**Work items:**
- Extract `MockDBSession` to `tests/helpers.py`
- `tests/test_admin_health.py` — invite CRUD (admin/non-admin/unauthed) + health check (connected/DB failure)
- `tests/test_tasks_unit.py` — `_run_pipeline_async` early exit, graph exception, ValidationError paths

### Phase 2: Correctness Fixes
**Rationale:** Two bugs with real production consequences. Both are narrow, isolated, and independently testable. SELECT FOR UPDATE must come before any feature that increases concurrency (rate limiting raises throughput, which increases race exposure). AsyncOpenAI fix is a prerequisite for the parallelization work item.
**Delivers:** Race-free session finalization; no connection accumulation in Celery workers
**Uses:** SQLAlchemy 2.x `with_for_update()`, `AsyncOpenAI` context manager API
**Avoids:** Identity map stale lock pitfall — use single-query approach, never mix `db.get()` with FOR UPDATE

**Work items:**
- `app/core/status.py` — replace `db.get()` + `db.refresh()` with `select(...).with_for_update()`
- `app/ingestion/embedder.py` — wrap `AsyncOpenAI` at function entry
- Extend `test_status_projection.py` or new file — CANCELLED guard and normal write path tests

### Phase 3: Security Surface Closure
**Rationale:** Rate limiting and bounds validation close abuse vectors. Kitchen config bounds should land before rate limiting so valid request shapes are enforced before throttling applies. RAG user assertion is independent and lowest-effort security improvement.
**Delivers:** Session creation throttling (per-user JWT key_func, not per-IP); unconstrained kitchen config inputs blocked; defense-in-depth on RAG data isolation
**Uses:** `slowapi 0.1.9` shared limiter, Pydantic v2 `@field_validator(mode="after")`, Pinecone metadata assertion
**Avoids:** Disconnected limiter pitfall, slowapi test isolation pitfall, Pydantic mode="before" TypeError, N+1 DB queries in RAG assertion

**Work items:**
- `app/core/limiter.py` — shared Limiter singleton
- `app/api/routes/sessions.py` — import shared limiter; add `@limiter.limit("10/hour")` with JWT-based key_func; add `request: Request` parameter
- `app/main.py` — import limiter from `app/core/limiter`; wire `app.state.limiter`
- Kitchen config model — `@field_validator(mode="after")` for numeric bounds
- `app/graph/nodes/enricher.py` — post-retrieval metadata assertion (log + drop, no raise)
- Rate limit tests with per-user JWT key_func and `autouse` reset fixture

### Phase 4: Performance (conditional on profiling)
**Rationale:** Both performance items require Phase 1-3 to be complete first. Embedding parallelization requires the AsyncOpenAI context manager fix (Phase 2). Scheduler optimization requires kitchen edge case tests as a regression gate. Profile before investing.
**Delivers:** Embedding fallback time reduced ~10x; Pinecone round trips reduced from N to 1 per pipeline run; scheduler slot-finding confirmed or improved
**Avoids:** `asyncio.gather` exception swallowing, interval boundary off-by-one, `asyncio.to_thread` event loop requirement

**Work items (conditional on profiling):**
- `app/ingestion/embedder.py` — `asyncio.gather(return_exceptions=True)` + `Semaphore(10)` in fallback loop
- `app/graph/nodes/enricher.py` — in-memory cache keyed `(rag_owner_key, query_text_hash)` per pipeline run
- `app/graph/nodes/dag_merger.py` — profile `_find_earliest_start()` first; replace linear stovetop scan only if confirmed O(n²)
- Gate: `test_phase6_unit.py` + `test_oven_temp_conflict.py` + `test_stovetop_heat_conflict.py` must all pass with identical fixture outputs

### Phase Ordering Rationale

- Tests before fixes: must confirm 99 tests green before any behavioral change; regressions visible immediately
- Kitchen bounds before rate limiting (Phase 3): valid request shape guaranteed before throttle enforced
- RAG assertion before RAG caching (Phase 3/4): cached results must also pass assertion; cache without assertion means cached cross-user chunks are never caught
- Scheduler optimization after kitchen edge case tests (Phase 4): edge case tests serve as regression gate for algorithm replacement
- AsyncOpenAI fix (Phase 2) before embedding parallelization (Phase 4): parallelization shares one client instance across coroutines — context manager fix establishes correct scope

### Research Flags

Phases with well-documented patterns (research-phase not needed):
- **Phase 1 (Test Infrastructure):** Exact patterns already exist in `test_api_routes.py` and `test_ingestion_tasks.py`; follow them directly
- **Phase 2 (Correctness Fixes):** SQLAlchemy `with_for_update()` and `AsyncOpenAI` context manager are fully documented; implementation is unambiguous
- **Phase 3 (Security):** slowapi and Pydantic v2 patterns are well-established; shared limiter module pattern documented in STACK.md

Phase warranting implementation care (not deep research):
- **Phase 4 (Performance):** Profile before implementing. If embedding parallelization is implemented, the `asyncio.gather` + `Semaphore` pattern requires testing with a mock that raises on partial chunks to confirm `return_exceptions=True` is correctly wired.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All patterns verified against installed library versions and existing codebase; no new dependencies |
| Features | HIGH | Direct codebase audit (source inspection of status.py, embedder.py, sessions.py, dag_merger.py); scope confirmed in PROJECT.md |
| Architecture | HIGH | Direct source analysis of all affected files; integration boundaries verified against existing test patterns |
| Pitfalls | HIGH | SQLAlchemy/asyncio/Pydantic pitfalls confirmed against installed versions; slowapi _storage.reset() API is MEDIUM — needs version verification |

**Overall confidence:** HIGH

### Gaps to Address

- **slowapi `_storage.reset()` API in 0.1.9:** The reset mechanism for in-memory storage needs verification against the exact installed version before writing the autouse fixture. Alternative: use `enabled=False` on a per-test Limiter instance and enable explicitly in rate limit tests only.
- **`KitchenConfig` model location:** Research references bounds validators for kitchen config but does not pinpoint which model file defines `KitchenConfig` or the exact field names. Locate and confirm before writing validators.
- **`ensure_admin_user()` assertion field:** Architecture research notes it "almost certainly reads a field from UserProfile" but does not confirm whether it checks `is_admin: bool` or email membership in `settings.admin_emails`. Inspect `app/core/auth.py` before writing admin test fixtures.
- **Scheduler O(n²) confirmation:** The `_IntervalIndex` may already resolve the main O(n²) path. Profile with a large input set (10+ recipes, 50+ steps) before investing in Phase 4 scheduler work.

## Sources

### Primary (HIGH confidence — direct codebase inspection)
- `app/core/status.py` lines 47–56 — TOCTOU race confirmed
- `app/ingestion/embedder.py` lines 72–112 — AsyncOpenAI leak and sequential fallback confirmed
- `app/graph/nodes/enricher.py` lines 330–358 — per-recipe Pinecone query and user_id filter confirmed
- `app/graph/nodes/dag_merger.py` lines 585–614 — `_IntervalIndex` presence and safety valve confirmed
- `app/api/routes/sessions.py` — disconnected `Limiter` instance confirmed
- `app/main.py` — `app.state.limiter` registration and Redis check confirmed
- `tests/test_admin_invites.py`, `tests/test_api_routes.py` — established async route test pattern confirmed
- `tests/test_ingestion_tasks.py` — established Celery inner-function test pattern confirmed
- `.planning/codebase/CONCERNS.md` — full concern catalog
- `.planning/PROJECT.md` — milestone scope and anti-feature decisions

### Secondary (HIGH confidence — installed library documentation)
- SQLAlchemy 2.x async docs: `select(...).with_for_update()` + identity map behavior
- Celery 5.4: `task_always_eager` removal confirmed; `task_max_retries=0` confirmed in `celery_app.py`
- httpx 0.27: `ASGITransport(app=app)` requirement confirmed
- Pydantic 2.7.4: `@field_validator(mode="after")` vs `mode="before"` ordering semantics
- asyncio stdlib (Python 3.12): `gather(return_exceptions=True)` semantics; `Semaphore` cancellation safety

### Tertiary (MEDIUM confidence — needs version verification)
- slowapi 0.1.9: `_storage.reset()` API for in-memory backend — version-specific, needs confirmation

---
*Research completed: 2026-04-08*
*Ready for roadmap: yes*
