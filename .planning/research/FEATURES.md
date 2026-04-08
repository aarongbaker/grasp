# Feature Landscape: Production Hardening Pass

**Domain:** FastAPI + Celery + LangGraph async Python API
**Researched:** 2026-04-08

---

## Table Stakes

Features users expect. Missing means production is unsafe, unreliable, or auditable systems will flag it.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **AsyncOpenAI client context manager** | Client leak in embedder.py (line 72) — no explicit close, relies on Celery process death for cleanup. Long-running embedding jobs on large cookbooks will silently accumulate dead connections. | Low | Wrap in `async with AsyncOpenAI(...) as client:` or explicit `.aclose()` in finally block. Confirmed: client created at module scope of function, never closed. |
| **SELECT FOR UPDATE in finalise_session()** | Race between pipeline finalization and user cancel at status.py lines 52–56. Current guard: `db.refresh()` then `if result.status == CANCELLED`. This window is real: refresh and status check are two separate operations with no DB lock. Double-write can corrupt terminal state. | Low-Medium | Requires `SELECT ... FOR UPDATE` wrapping the read + write in a single transaction. asyncpg already installed. |
| **Pydantic bounds validators on kitchen config** | Users POST arbitrary kitchen config — zero burners, 9999 burners, negative oven count. Scheduler's O(n²) loop hits the 10,000-iteration safety valve on absurd inputs. No upper bounds defined on burner count, equipment list size, or config string lengths. | Low | Pydantic v2 field validators (`@field_validator`, `ge=`, `le=`). Already on Pydantic 2.7.4. |
| **Rate limiting on POST /sessions** | No throttle on session creation. Each session enqueues a Celery task that runs multiple LLM calls — a single user can trigger dozens of pipeline runs in seconds. Celery task queue provides no backpressure guarantee. | Low | slowapi 0.1.9 already installed. Add `@limiter.limit("5/minute")` decorator to `create_session()`. |
| **Admin route test coverage** | Admin invite flow is active in production with zero tests. A broken invite route would silently fail new user onboarding. Health check endpoint has no test — a deadlocked DB query would only be discovered by monitoring, not CI. | Medium | Need `TestClient` tests for `/api/v1/admin/*` invite endpoints and `/api/v1/health`. |
| **Celery task retry/failure test coverage** | Task retry logic, failure callbacks, and timeout handling in tasks.py are untested. Silent task hang means sessions stay in `GENERATING` state forever; duplicate retry could double-write or create duplicate sessions. | Medium | Mock Celery worker with `task_always_eager=True` for retry path tests; mock `graph.ainvoke` raising exceptions. |

**Dependency chain for table-stakes:**
- Kitchen bounds validators should land before rate limiting — rate limiting without bounds means valid requests can still send unconstrained configs.
- SELECT FOR UPDATE requires no other prerequisite but should be isolated in its own transaction wrapper test.

---

## Differentiators

Worth doing. Improves reliability meaningfully but production is not immediately unsafe without them.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **RAG chunk user_id server-side assertion** | Pinecone metadata filter (`rag_owner_key`) is the only guard preventing cross-user recipe data leakage. If a metadata bypass occurs (malformed filter, Pinecone SDK bug), enricher silently ingests another user's cookbook. Adding a post-retrieval assertion that every returned chunk's user_id matches the requesting user is cheap defense-in-depth. | Low | Post-process `results.matches` in enricher.py lines 338–350 to assert `match.metadata.get("user_id") == user_id`. Log and drop mismatched chunks, don't raise (recoverable). |
| **Equipment CRUD + dag_merger constraint unlock test coverage** | Equipment CRUD endpoints (POST/DELETE on users.py) have no tests. Data corruption on equipment delete (cascade vs. orphaned FK) would only surface in production. Equipment unlock path in dag_merger (the interval management for custom equipment) is also untested — a regression here silently produces wrong schedules. | Medium | Tests for equipment add/delete API routes; unit tests for equipment-interval logic in dag_merger. |
| **Kitchen edge case coverage** | Zero-burner kitchen, missing kitchen config, invalid burner descriptor strings — scheduler's loop depends on these being valid. Untested edge cases means a misconfigured user profile silently produces a bad schedule or hits the 10,000-iteration safety valve. | Medium | Parametrized pytest tests feeding edge-case kitchen configs through dag_merger's scheduling loop directly (no need to run full pipeline). |
| **Interval-based time-slot search in scheduler** | Current greedy loop tries up to 10,000 candidate start times, O(n²) per step. For complex 5-course dinners (~50 steps, multiple resource constraints), this is measurable latency. Replacing with binary search over conflict intervals reduces worst case to O(n log n). The interval index structure is already partially present (the `count_overlapping` call at line 594). | High | Requires restructuring `_find_earliest_slot()` to walk interval endpoints instead of iterating candidates. Compatible with existing greedy approach — pure algorithm replacement, no state change. |
| **asyncio.gather with semaphore for embedding fallback** | Fallback per-chunk embedding loop (embedder.py lines 103–112) is sequential — 50 chunks × ~300ms each = 15 seconds of blocking work when a batch fails. Parallelizing with `asyncio.gather` + `asyncio.Semaphore(10)` keeps concurrency bounded (avoiding OpenAI rate limits) while reducing fallback time ~10x. | Low-Medium | asyncio is stdlib. Bounded semaphore is 3 lines of wrapper code. Requires wrapping per-chunk calls in tasks and gathering. |
| **Batch or cache RAG context retrieval in enricher** | Each recipe triggers a separate Pinecone query at lines 620–630. For a 5-course dinner, that's 5 round trips to Pinecone (each ~100–300ms). Caching cookbook context keyed by (user_id, session_id) across recipes in the same pipeline run eliminates N-1 redundant queries. Pinecone does not natively batch queries per the SDK docs. | Medium | In-memory dict cache keyed `(rag_owner_key, query_text_hash)` scoped to the enricher node invocation. No persistent cache needed — single-run cache only. |

**Dependency chain for differentiators:**
- RAG user assertion has no prerequisites and is the lowest-effort security improvement after the table-stakes security fixes.
- Interval-based scheduler should come after kitchen edge case tests exist — the tests serve as a regression suite for the replacement algorithm.
- Embedding fallback parallelization is independent and can be done in any order.
- RAG caching should come after RAG user assertion (assertion logic will need to be applied to cached results too).

---

## Anti-Features

Things that sound good but add complexity without value at this hardening stage.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **LangGraph checkpoint migration** | Appealing to address now given version pin risk, but LangGraph checkpoint schema is internal and migration requires a full export/re-import script with no rollback path. Zero production incidents from this; it's pre-emptive work for a hypothetical upgrade. Risk of introducing data corruption exceeds risk of staying pinned. | Stay on pinned LangGraph version. Document the migration plan in CONCERNS.md. Address when an actual upgrade is needed. |
| **Base64 PDF → object storage migration** | Correct long-term solution, but requires new infrastructure (S3/GCS), new IAM policies, Celery task refactor, and ingestion route changes. This is a scaling fix for "100+ concurrent ingestion jobs" — a load the current user base doesn't reach. Adding S3 now adds a new external dependency and deployment surface. | Keep base64 in queue. Add a comment with the migration plan. Revisit when ingestion volume makes it measurably painful. |
| **Session cancellation with task revocation** | Celery task revocation with cleanup hooks requires signal handling, cleanup coroutines, and careful idempotency. The existing cancel path (PATCH /sessions/{id}) sets status to CANCELLED and the pipeline checks it at finalise_session. That's sufficient for user-facing correctness. Full revocation adds significant complexity for a feature users rarely invoke. | The SELECT FOR UPDATE fix (table stakes) already closes the double-write risk on cancellation. That's the correct scope. |
| **Enricher partial recovery threshold config** | CONCERNS.md flags that a single enricher failure kills all enrichment. Adding an N-failure threshold via config sounds defensive, but the current per-recipe isolation already handles the common case. Making the threshold configurable adds a tuneable that users don't understand and ops won't set correctly. | The existing per-recipe recoverable model is correct. If all recipes fail → fatal is the right behavior. Don't add config knobs for this. |
| **PgBouncer / explicit SQLAlchemy pool config** | Zero evidence of connection pool exhaustion in current usage. Adding PgBouncer is an infrastructure dependency; configuring pool sizes requires load testing. Premature optimization that adds operational complexity. | Address if connection exhaustion is observed in monitoring. Note it in scaling runbook. |
| **Dietary restriction enforcement** | Already explicitly out of scope in PROJECT.md. Any enforcement touches the generator prompt, adds a validation node, and requires a new recipe-rejection/retry loop. This is a new feature, not hardening. | Defer to a separate feature milestone. |

---

## Feature Dependencies

```
Kitchen bounds validators
  → Rate limiting (valid request shape guaranteed before throttle)

RAG user assertion
  → RAG context cache (cached results must also be asserted)

Kitchen edge case tests
  → Interval-based scheduler replacement (edge cases serve as regression suite)

SELECT FOR UPDATE (table stakes)
  → No other prerequisite; blocks nothing

AsyncOpenAI context manager (table stakes)
  → No other prerequisite; blocks nothing

Admin/Celery test coverage (table stakes)
  → No prerequisite; independently testable
```

---

## MVP Recommendation

Prioritize in this order:

1. **AsyncOpenAI client context manager** — one-line risk, two-line fix, O(1) effort
2. **SELECT FOR UPDATE in finalise_session()** — real race, simple fix, prevents data corruption
3. **Pydantic bounds validators on kitchen config** — closes scheduler abuse vector
4. **Rate limiting on POST /sessions** — slowapi already installed, one decorator
5. **Admin + health route tests** — production code path with zero coverage
6. **Celery retry/failure tests** — task silencing is a genuine production risk
7. **RAG chunk user assertion** — cheapest security improvement remaining
8. **Embedding fallback parallelization** — high impact, low code change
9. **Equipment CRUD + merger tests** — medium effort, closes data corruption risk
10. **Kitchen edge case tests** — prerequisite for scheduler refactor
11. **RAG context cache** — eliminates N+1 after assertion is in place
12. **Interval-based scheduler** — do last; requires edge case tests as safety net first

Defer: Object storage, checkpoint migration, session revocation, dietary restrictions, connection pooling.

---

## Sources

- `/Users/aaronbaker/Desktop/Projects/grasp/.planning/codebase/CONCERNS.md` — full concern catalog (HIGH confidence — direct code audit)
- `/Users/aaronbaker/Desktop/Projects/grasp/.planning/PROJECT.md` — milestone scope and constraints (HIGH confidence)
- `/Users/aaronbaker/Desktop/Projects/grasp/.planning/codebase/ARCHITECTURE.md` — system structure (HIGH confidence)
- `/Users/aaronbaker/Desktop/Projects/grasp/app/ingestion/embedder.py` lines 72–112 — confirmed AsyncOpenAI leak and sequential fallback (HIGH confidence — direct source inspection)
- `/Users/aaronbaker/Desktop/Projects/grasp/app/core/status.py` lines 45–60 — confirmed race condition pattern (HIGH confidence — direct source inspection)
- `/Users/aaronbaker/Desktop/Projects/grasp/app/graph/nodes/enricher.py` lines 330–358 — confirmed per-recipe Pinecone query and user_id filter pattern (HIGH confidence)
- `/Users/aaronbaker/Desktop/Projects/grasp/app/graph/nodes/dag_merger.py` lines 585–614 — confirmed O(n²) loop with 10,000 safety valve (HIGH confidence)
