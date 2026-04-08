# Domain Pitfalls

**Domain:** Python async API hardening — SQLAlchemy, slowapi, AsyncOpenAI, greedy scheduler, asyncio concurrency, RAG isolation
**Researched:** 2026-04-08
**Project:** grasp hardening milestone

---

## Critical Pitfalls

Mistakes that cause rewrites, silent data corruption, or test suite destruction.

---

### Pitfall 1: SELECT FOR UPDATE on an already-fetched object (stale lock)

**What goes wrong:** `finalise_session()` in `app/core/status.py` calls `db.get(Session, session_id)` then `db.refresh(result)` before checking for cancellation. The naive fix is to add `with_for_update()` to the refresh call — but `db.refresh()` in SQLAlchemy async does not accept `with_for_update`. Developers try to chain `.with_for_update()` onto refresh, get a TypeError, then fall back to a plain `SELECT ... FOR UPDATE` using `db.execute(select(Session).where(...).with_for_update())`. The pitfall is forgetting that the *first* `db.get()` call (line 47) already loaded the object into the session identity map — the subsequent `SELECT FOR UPDATE` returns the database row but the session may still hold the original stale in-memory instance. The lock is acquired but the status check reads cached memory, not the locked row.

**Why it happens:** SQLAlchemy's unit-of-work identity map caches objects by primary key. If `db.get(Session, session_id)` already ran in the same session, a subsequent `db.execute(select(Session).where(...).with_for_update())` acquires the DB lock but `scalars().first()` returns the already-cached object unless the session is explicitly expired or `populate_existing=True` is passed to the select.

**Consequences:** The cancellation check on line 55 (`if result.status == SessionStatus.CANCELLED`) reads the pre-lock status. Pipeline finalisation writes COMPLETE over a CANCELLED session. Race condition survives the fix.

**Prevention:**
- Replace the two-step `db.get()` + `db.refresh()` with a single `db.execute(select(Session).where(Session.id == session_id).with_for_update())` using `execution_options(populate_existing=True)` or expire the object first.
- Preferred pattern:
  ```python
  stmt = select(Session).where(Session.id == session_id).with_for_update()
  result = await db.execute(stmt)
  row = result.scalars().first()
  ```
  Do not call `db.get()` anywhere in the same function — let the FOR UPDATE query be the first access.
- Wrap the entire check-then-write in an explicit transaction block so the lock is held until commit.

**Detection:** Write a test that concurrently calls `finalise_session()` and a cancellation PATCH. If both write without a lock, one silently wins. A test using `asyncio.gather` with a small `asyncio.sleep(0)` between them will reproduce the race without actual concurrency infrastructure.

**Phase:** Security/Bug Fix — SELECT FOR UPDATE work item.

---

### Pitfall 2: slowapi `key_func` with JWT gives IP-based limits, not per-user limits

**What goes wrong:** The current code in `sessions.py` (line 46) creates `limiter = Limiter(key_func=get_remote_address)`. Adding `@limiter.limit("30/minute")` to `POST /sessions` only enforces 30 requests per minute per *IP address*. Behind a NAT, CDN, or load balancer, all users share one IP and the limit becomes meaningless. The stated goal is per-user session quotas — IP-based limits do not satisfy this.

**Why it happens:** `get_remote_address` is slowapi's default helper and every tutorial shows it. Per-user limits require a custom `key_func` that extracts the user identifier from the request. Since `create_session` requires a JWT, the user id is available — but `key_func` receives only a `Request` object, so the JWT must be decoded inside it without raising (slowapi silently falls back to IP if the key_func raises).

**Consequences:** A determined attacker behind their own IP can still spam sessions. A shared office NAT means 30 legitimate users effectively share one allowance.

**Prevention:**
- Define a custom `key_func` that decodes the JWT from `request.headers.get("Authorization")` and returns the `sub` claim (user_id). Wrap in try/except and fall back to `get_remote_address(request)` on decode failure so unauthenticated abuse is still rate-limited.
- The `limiter` instance in `sessions.py` must be the same instance registered on `app.state.limiter` — or use `app.state.limiter` directly. Creating a separate `Limiter()` in the route file (as currently done) works only if slowapi's `SlowAPIMiddleware` is not used; double-check which integration path is live in `main.py`. The existing `main.py` attaches `app.state.limiter` but the route file creates its own separate instance — this silently means the route's limiter uses in-memory storage only, never the Redis-backed global limiter.
- Set the limit on the decorator to a per-user value (e.g. `"10/hour"` for session creation) not a per-minute burst.

**Detection:** In tests, mock the JWT sub claim to two different user IDs; send 35 requests from the same "IP" alternating users. Under IP-based limiting both users get blocked at 30. Under correct user-based limiting each gets their own 10/hour window.

**Warning signs:** Test suite uses `AsyncClient(app=app)` with `base_url="http://test"` — TestClient does not set `X-Forwarded-For`, so all test requests land on `127.0.0.1`. Rate limit tests will pass even with a broken key_func unless the test explicitly sends requests from multiple distinct user tokens.

**Phase:** Security — Rate limiting work item.

---

### Pitfall 3: `AsyncOpenAI` context manager wrapping the entire batch loop breaks on partial failure

**What goes wrong:** The obvious fix for the resource leak at line 72 of `embedder.py` is to change:
```python
openai_client = AsyncOpenAI(...)
```
to:
```python
async with AsyncOpenAI(...) as openai_client:
    # ... entire batch loop ...
```
This works, but wrapping the outer `for batch_start in range(...)` loop means a single batch failure that hits the `except Exception` path and continues the loop will keep operating on a client whose underlying `httpx.AsyncClient` may or may not still be healthy depending on the error type. The more insidious failure is wrapping *only* the inner fallback loop: if the context manager is opened and closed for each per-chunk call, the repeated open/close of `httpx.AsyncClient` inside tight loops causes connection churn and is slower than the original sequential code.

**Why it happens:** `AsyncOpenAI.__aenter__` / `__aexit__` close the underlying HTTP client. Wrapping it per-chunk (inside the fallback loop) creates a new TCP connection for every chunk — defeating the purpose of async connection pooling.

**Consequences:** Per-chunk context manager wrapping causes 10-50x slower fallback embedding. The connection pool benefit disappears. Under high concurrency this can exhaust OS file descriptors.

**Prevention:**
- Wrap `AsyncOpenAI` once at function entry, outside all loops:
  ```python
  async with AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0) as openai_client:
      for batch_start in range(0, len(chunks), _EMBED_BATCH_SIZE):
          ...
  ```
- The `finally` path is handled by the context manager — do not add a redundant `try/finally: await openai_client.aclose()`.
- When parallelising with `asyncio.gather`, pass the *same* client instance to all coroutines — do not create one client per coroutine.

**Detection:** Check for `AsyncOpenAI(` appearing more than once in the function body or inside a loop. Any `AsyncOpenAI` construction inside a `for` or `while` block is wrong.

**Phase:** Bug Fix — AsyncOpenAI resource leak. Also applies to parallelisation work item.

---

### Pitfall 4: `asyncio.gather` with semaphore swallows the first exception and leaks the rest

**What goes wrong:** The parallelisation fix for `embedder.py` will use `asyncio.gather` with a `asyncio.Semaphore`. The default `asyncio.gather(*coros)` behaviour raises the first exception and cancels nothing — remaining coroutines continue running but their results (and exceptions) are silently discarded. The caller gets one exception but potentially N-1 orphaned coroutines still holding semaphore slots and making HTTP requests.

**Why it happens:** `asyncio.gather` does not cancel sibling tasks by default. `return_exceptions=True` collects all results/exceptions but requires the caller to inspect the list for `BaseException` instances. When `return_exceptions=False` (default), the first exception propagates but siblings keep running until they complete or the event loop closes.

**Consequences in embedder.py:** If three concurrent embedding requests fail (OpenAI rate limit burst), `gather` raises on the first, but the other two continue consuming rate-limit quota. The Semaphore is not released if the guarded coroutine raises without entering the `async with` block (if `Semaphore.acquire()` itself times out or is cancelled mid-acquire).

**Prevention:**
- Use `return_exceptions=True` and post-filter results:
  ```python
  results = await asyncio.gather(*coros, return_exceptions=True)
  embeddings = [r if not isinstance(r, BaseException) else None for r in results]
  ```
- Alternatively, use `asyncio.TaskGroup` (Python 3.11+) which cancels all sibling tasks on first failure — cleaner but changes error semantics.
- Ensure the semaphore is always acquired via `async with sem:` (not `await sem.acquire()` + manual release) so cancellation cannot leave the semaphore permanently decremented.
- Set the semaphore bound to match OpenAI's requests-per-minute limit at the per-process level, not per-task: for `text-embedding-3-small`, 3000 RPM means ~50 concurrent requests is safe; use `asyncio.Semaphore(10)` as a conservative default.

**Detection:** Test the fallback path with a mock that raises on 2 of 5 chunks. Assert that the returned embedding list has `None` entries for the failed chunks (not that the function raises). If it raises, `return_exceptions=True` is missing.

**Phase:** Performance — embedding parallelisation work item.

---

## Moderate Pitfalls

Mistakes that produce wrong results or subtle test failures without obvious errors.

---

### Pitfall 5: Interval-based scheduler correctness — off-by-one at interval boundaries

**What goes wrong:** The `_IntervalIndex` already uses bisect for O(log n) overlap counting and `min_end_after` for time advancement. The "O(n²) fix" being planned is replacing the 10,000-iteration `_find_earliest_start` loop with a smarter advancement strategy. The pitfall is an off-by-one error in the boundary condition: `count_overlapping(candidate, window_end)` uses `bisect_left` on starts and `bisect_right` on ends, giving a half-open interval `[start, end)`. If a step ends exactly at `candidate` (end == candidate), it is *not* counted as overlapping — which is correct (that step has vacated its resource). But if the new slot-finding logic uses `bisect_right` on ends with `> t` semantics and `min_end_after` returns the interval ending exactly *at* `candidate`, advancing to that same `candidate` creates an infinite loop: `candidate` doesn't move, the interval at exactly `candidate` is still "active" in the `_OvenInterval` list (which uses `interval.end > candidate` for overlap checks, a strict inequality).

**Why it happens:** `_IntervalIndex.count_overlapping` and the manual loop over `oven_intervals` use different boundary semantics. `count_overlapping` treats intervals as `[start, end)` (end not included). The `oven_intervals` loop at line 611 uses `interval.end > candidate` (strict) — these are consistent. But `min_end_after` returns `self._ends[idx]` where `idx = bisect_right(self._ends, t)` — this returns the smallest end *strictly greater* than `t`. If a previous fix changes this to `bisect_left` (common mistake when "fixing" the boundary), it returns the smallest end `>= t`, which can return the same `t` repeatedly, causing the loop to not advance.

**Consequences:** Scheduler enters an infinite loop that the 10,000-iteration safety valve catches, but now the safety valve is gone. Without the safety valve, the scheduler either hangs indefinitely or the process is killed by the Celery task timeout.

**Prevention:**
- Keep the safety valve (even if lower: 1,000 iterations) during development; remove only after all test fixtures pass with it never triggering.
- Add a property test: generate random step sets with known solutions and verify the scheduler terminates and produces a valid schedule in O(n log n) time.
- When modifying `min_end_after`, explicitly test the case where `t` equals an existing end value — the return must be strictly greater than `t`, not `>= t`.
- The `_OvenInterval` list is separate from `_IntervalIndex` and is iterated linearly. If n grows large, this linear scan reintroduces O(n) behaviour per iteration even if `_IntervalIndex` is O(log n). Ensure the oven conflict path also uses a sorted index if many oven steps exist.

**Detection:** Test fixture: two recipes both using OVEN starting at minute 0 for 30 minutes. The second should be scheduled at minute 30. If `start == 30` rather than `> 30`, the boundary is wrong.

**Phase:** Performance — O(n²) scheduler work item.

---

### Pitfall 6: RAG user_id assertion introduces N+1 DB queries if done naively

**What goes wrong:** Adding a server-side assertion that retrieved Pinecone chunks belong to the requesting user (enricher.py lines 338–395) is the right security fix. The naive implementation queries the database for each chunk to verify ownership: `await db.execute(select(CookbookChunk).where(CookbookChunk.chunk_id == chunk_id))` inside the chunk loop. With `rag_retrieval_top_k` typically set to 5–10, this adds 5–10 DB queries per recipe enrichment call. For a 4-course menu with parallel enrichment, that is up to 40 additional DB round-trips in the hot path.

**Why it happens:** The chunk metadata returned by Pinecone already contains `user_id` and `rag_owner_key` in the `metadata` dict (see embedder.py line 137–139). Developers reaching for the DB to validate ownership overlook that Pinecone metadata is authoritative (it was written at ingest time with the same user_id used to filter the query). The filter itself (`owner_filter` on line 348–351) already enforces ownership at the Pinecone layer.

**Consequences:** 40 extra DB queries per pipeline run; increased latency in the enricher; potential DB connection exhaustion under load. For the Celery worker running many concurrent enrichments, this can exhaust the async connection pool.

**Prevention:**
- The assertion should be metadata-only: compare `metadata.get("user_id")` from the Pinecone response against the `user_id` passed into `_retrieve_rag_context`. No DB query needed. This is a defence-in-depth check against metadata bypass, not an authoritative DB lookup.
- If a DB round-trip is genuinely required (e.g. for audit logging), batch it: collect all chunk_ids from a single retrieval call and issue one `SELECT ... WHERE chunk_id IN (...)` query, not one per chunk.
- Log assertion failures as `WARNING` (not `ERROR`) and filter the offending chunks rather than aborting — the graceful degradation contract for RAG retrieval already returns `[]` on failure; a partial chunk set is better than no enrichment.

**Detection:** Count DB queries in an enrichment test using SQLAlchemy's `echo=True` or a query counter fixture. A single `_retrieve_rag_context` call should produce 0 additional DB queries.

**Phase:** Security — RAG user_id assertion work item.

---

### Pitfall 7: slowapi test isolation — shared in-memory limiter state bleeds between tests

**What goes wrong:** `sessions.py` creates `limiter = Limiter(key_func=get_remote_address)` at module import time. In tests that use `AsyncClient(app=app)`, the FastAPI application is created once per test session (or per test, depending on fixture scope). The in-memory limiter stores hit counts in a module-level dict. Unless the limiter is reset between tests, a rate limit test that exhausts the limit will cause the next test in the same process to get 429s on the first request — causing cascading unrelated failures that look like network errors.

**Why it happens:** slowapi's in-memory backend (`MemoryStorage`) is a dictionary keyed on `"{route}:{key}"`. It persists across requests in the same process. `AsyncClient` does not restart the process or reset module state between test functions.

**Consequences:** Test ordering determines pass/fail. Tests pass in isolation but fail when run as a suite. This is particularly insidious because `pytest -k test_create_session` passes but `pytest tests/` fails.

**Prevention:**
- In the test fixture that sets up the `AsyncClient`, reset the limiter storage after each test:
  ```python
  @pytest.fixture(autouse=True)
  def reset_rate_limiter():
      yield
      # Clear in-memory storage between tests
      if hasattr(app.state, "limiter"):
          app.state.limiter._storage.reset()
  ```
- Alternatively, configure the test app with `Limiter(key_func=get_remote_address, enabled=False)` and have a dedicated rate limit test that enables limiting explicitly.
- Never run rate limit correctness tests and functional flow tests in the same limiter state.

**Detection:** Run tests twice in different orders (`pytest --randomly-seed=12345` vs default). If rate limit tests pass in one order but fail in another, state bleed is the cause.

**Phase:** Security — Rate limiting work item (test setup).

---

## Minor Pitfalls

Mistakes that cause annoying but recoverable failures.

---

### Pitfall 8: `asyncio.to_thread` in Celery workers with a custom event loop

**What goes wrong:** `embedder.py` (line 148) calls `await asyncio.to_thread(index.upsert, ...)` inside `embed_and_upsert_chunks`. This is called from a Celery task via an `asyncio.run()` wrapper (or `async_to_sync`). In Celery workers, the event loop is typically created fresh per task via `asyncio.run()`. `asyncio.to_thread` requires a running event loop with a `ThreadPoolExecutor` attached. This works correctly in Python 3.10+ via `asyncio.run()`, but if the calling Celery task uses `loop.run_until_complete()` with a manually created loop (common in older Celery integrations), `asyncio.to_thread` may fail with `RuntimeError: no running event loop`.

**Why it happens:** The Celery/asyncio integration in grasp (via `tasks.py`) likely uses a pattern similar to `asyncio.get_event_loop().run_until_complete(coro)`. This is not the same as `asyncio.run()`, which ensures the thread executor is properly configured.

**Prevention:** Ensure `tasks.py` uses `asyncio.run(coro)` (not `loop.run_until_complete`) when invoking async functions. `asyncio.run()` always creates a fresh loop with a properly configured `ThreadPoolExecutor`.

**Detection:** Run the Celery task under test with `asyncio.run()` vs `loop.run_until_complete()` and confirm both paths reach the Pinecone upsert line.

**Phase:** Performance — embedding parallelisation (touches the same function).

---

### Pitfall 9: Pydantic bounds validators on kitchen config — model vs field validator ordering

**What goes wrong:** Adding `@field_validator` or `@model_validator` to the `KitchenConfig` model (or wherever kitchen config is validated in `users.py`) for bounds checking (max equipment count, max burners) can silently fail if the model is instantiated with `model_validate(dict)` where the dict contains raw strings that haven't been coerced. Pydantic v2 runs field validators *after* type coercion, so a string `"10"` passed for an `int` field will be coerced to `10` before the `@field_validator` sees it — this is correct. But if the validator uses `@model_validator(mode="before")` and checks the raw dict, it sees `"10"` as a string and the numeric comparison `value > MAX_BURNERS` raises a `TypeError`.

**Why it happens:** Mixing `mode="before"` and `mode="after"` validators without understanding when coercion runs is a common Pydantic v2 mistake. `mode="before"` runs on raw input; `mode="after"` runs on the already-coerced model instance.

**Prevention:** Use `@field_validator("max_burners", mode="after")` for numeric range checks — at that point the value is guaranteed to be an `int`. Use `mode="before"` only for type coercion, not validation logic.

**Detection:** Write a test that passes `{"max_burners": "999"}` (string) to the validator and assert it raises the correct validation error, not a `TypeError`.

**Phase:** Security — Pydantic bounds validators work item.

---

### Pitfall 10: `WITH_FOR_UPDATE` on LangGraph checkpoint tables causes lock contention with status_projection

**What goes wrong:** `finalise_session()` acquires a `SELECT FOR UPDATE` lock on the Session row. If `status_projection()` is called concurrently (e.g. the frontend polls `GET /sessions/{id}` while the pipeline is finishing), and if `status_projection()` also reads from the same DB session with any shared lock, lock contention can delay the status response. This is less likely with the current code (status_projection reads from LangGraph checkpoints via `graph.aget_state()`, not the Session row), but if the implementation is changed to query the Session row for status too, contention becomes possible.

**Prevention:** The `SELECT FOR UPDATE` lock in `finalise_session()` must only be held for the duration of the check-then-write transaction, which should complete in milliseconds. Do not read unrelated data inside the locked transaction. Keep `status_projection()` reading from the LangGraph checkpoint (not the locked Session row) to avoid contention entirely.

**Phase:** Bug Fix — SELECT FOR UPDATE work item.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|----------------|------------|
| SELECT FOR UPDATE | Identity map cache returns stale data despite acquiring lock | Use `populate_existing=True` or single-query approach; never mix `db.get()` + `with_for_update()` |
| slowapi per-user limiting | Separate `Limiter()` instances in route files bypass Redis backend | Use `request.app.state.limiter` rather than module-level `limiter = Limiter()` in route files |
| slowapi test isolation | In-memory limiter state persists between tests causing ordering-dependent failures | Reset `limiter._storage` between tests or disable limiting in non-rate-limit tests |
| AsyncOpenAI context manager | Client opened per-chunk in fallback loop causes connection churn | One `async with AsyncOpenAI()` at function entry; same instance passed to all coroutines |
| asyncio.gather semaphore | First exception leaves siblings running; semaphore slots not released on cancellation | Use `return_exceptions=True`; `async with sem:` not manual acquire/release |
| Interval scheduler boundary | `min_end_after` semantics change (bisect_left vs bisect_right) causes infinite loop | Keep safety valve during dev; property-test boundary: end == candidate must not re-select same candidate |
| RAG user_id assertion | DB query per chunk creates N+1 pattern in enrichment hot path | Assert from Pinecone metadata, not DB; batch if DB round-trip genuinely needed |
| Pydantic bounds validators | `mode="before"` validators receive strings, numeric comparisons raise TypeError | Use `mode="after"` for all numeric range checks |

---

## Sources

- Direct codebase audit: `app/core/status.py`, `app/ingestion/embedder.py`, `app/graph/nodes/dag_merger.py`, `app/graph/nodes/enricher.py`, `app/api/routes/sessions.py`, `app/main.py`
- SQLAlchemy async docs: `with_for_update()` on `select()` + identity map behaviour (training knowledge, HIGH confidence for SQLAlchemy 2.x patterns)
- slowapi 0.1.9 source: `key_func` contract, `MemoryStorage` state persistence (MEDIUM confidence — version installed; storage reset API needs verification against installed version)
- asyncio docs: `gather(return_exceptions=True)` semantics, `Semaphore` cancellation safety (HIGH confidence — stable Python 3.12 stdlib)
- Pydantic v2 validator ordering: `mode="before"` vs `mode="after"` docs (HIGH confidence — Pydantic 2.7.4 installed)
