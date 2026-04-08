# Architecture Patterns — Hardening Milestone

**Domain:** Python API hardening (FastAPI + LangGraph + Celery)
**Researched:** 2026-04-08
**Confidence:** HIGH — based on direct codebase analysis

---

## Recommended Architecture for Hardening Work

The hardening items divide cleanly into four component groups. Each group has distinct
integration boundaries. Understanding the boundaries determines build order.

### Component Boundaries

| Component | File(s) | Responsibility | Couples To |
|-----------|---------|---------------|-----------|
| Admin/Health test layer | `tests/test_admin_health.py` (new) | HTTP-level contract tests for admin + health routes | `app.core.auth.ensure_admin_user`, `app.core.deps`, FastAPI `app_override` pattern |
| Celery task test layer | `tests/test_tasks_unit.py` (new) | Task logic tests without Redis | `app.workers.tasks._run_pipeline_async`, `app.core.status.finalise_session` |
| finalise_session() race fix | `app/core/status.py` lines 47–56 | Atomic read-then-write of Session row | SQLAlchemy `with_for_update()`, `AsyncSession` transaction boundary |
| Scheduler interval replacement | `app/graph/nodes/dag_merger.py` | O(log n) slot search replacing iteration loop | `_IntervalIndex` (already exists), `_find_earliest_start()`, `tests/fixtures/schedules.py` |

---

## Detailed Analysis Per Question

### 1. Admin + Health Route Test Fixtures

**What the routes need:**

- `admin.py` — requires `CurrentUser` (JWT) + `ensure_admin_user()` check + `DBSession` (for `Invite` insert)
- `health.py` — requires `DBSession` for `SELECT 1`; no auth

**Pattern already established in `tests/test_api_routes.py`:**

The existing route test file uses three interlocking tools:
1. `_create_test_app()` — builds a `FastAPI()` with no lifespan, includes only the routers under test
2. `MockDBSession` — in-memory fake with `add/commit/refresh/get/exec` stubs
3. `app.dependency_overrides` — replaces `get_session` and `get_current_user` with test-controlled callables

The admin tests need one additional fixture: a fake admin `UserProfile` with a flag that satisfies `ensure_admin_user()`. Check what `ensure_admin_user` asserts — it almost certainly reads a field from `UserProfile` (e.g., `is_admin: bool` or email membership in `settings.admin_emails`). Match that in `_make_admin_user()`.

**Recommended fixture structure for `tests/test_admin_health.py`:**

```python
def _create_admin_test_app() -> FastAPI:
    from app.api.routes.admin import router as admin_router
    from app.api.routes.health import router as health_router
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(health_router, prefix="/api/v1")
    return app


def _make_admin_user() -> UserProfile:
    # Populate whatever field ensure_admin_user() checks
    return UserProfile(user_id=uuid.uuid4(), is_admin=True, ...)


@pytest.fixture
def mock_db() -> MockDBSession:
    # Reuse MockDBSession from test_api_routes or extract to conftest
    return MockDBSession()


@pytest_asyncio.fixture
async def admin_client(mock_db):
    app = _create_admin_test_app()
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: _make_admin_user()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

**Health check:** Health needs a real `SELECT 1` path and a DB-failure path. The DB-failure path can be tested by injecting a mock DB whose `execute()` raises an exception and asserting a 503 response. The real DB path only needs MockDBSession with a passthrough `execute()`.

**Key constraint:** `MockDBSession` in `test_api_routes.py` does not implement `execute()` (only `exec()`). The health check uses `db.execute(text("SELECT 1"))` via a raw SQLAlchemy method. Either extend `MockDBSession` to add `async def execute(self, stmt): return None`, or create a separate `MockRawDBSession` for health tests. Prefer extending the existing class to avoid duplication — extract `MockDBSession` to `tests/helpers.py` so both test files share it.

**DB dependency for admin:** The `create_invite` handler calls `db.add()`, `db.commit()`, and `db.refresh()`. `MockDBSession` already handles all three. No real Postgres needed. The `InviteResponse` includes `created_at` and `expires_at` — `MockDBSession.refresh()` will need to handle `Invite` objects (it currently only handles `session_id`, `job_id`, `recipe_id`, `cookbook_id`, `user_id` primary keys). Add `invite_code` or `id` detection, or use a simpler approach: pre-seed the invite in `mock_db._store` before the request.

---

### 2. Celery Task Testing — Eager Mode vs Mocking

**What `tasks.py` actually does:**

`run_grasp_pipeline()` is a thin synchronous wrapper that calls `asyncio.run(_run_pipeline_async())`. The real logic is entirely in `_run_pipeline_async()` — it creates its own `AsyncPostgresSaver`, engine, `SessionLocal`, loads DB rows, calls `graph.ainvoke()`, and calls `finalise_session()`.

**Recommendation: Mock `_run_pipeline_async` directly, not Celery.**

Celery eager mode (`CELERY_TASK_ALWAYS_EAGER = True`) runs the task synchronously in the same process. This is useful for integration tests but still requires the internal DB + broker to be non-failing. For unit testing retry logic, failure callbacks, and timeout handling, eager mode is insufficient — it does not simulate broker failures, worker crashes, or Celery's retry machinery.

The cleanest approach for this codebase:

**Unit tests of task logic:** Test `_run_pipeline_async()` directly as an async function, patching its internal dependencies. This exercises the real error handling (ValidationError catch, generic Exception catch, `finalise_session()` calls on both paths) without Celery or Redis.

```python
@pytest.mark.asyncio
async def test_pipeline_finalises_on_validation_error():
    with patch("app.workers.tasks.finalise_session") as mock_finalise:
        with patch("app.workers.tasks.build_session_initial_state", side_effect=ValidationError(...)):
            await _run_pipeline_async(str(session_id), str(user_id))
    mock_finalise.assert_awaited_once()
    # Assert the state passed to finalise_session has recoverable=False error
```

**Integration tests of the Celery task wrapper:** Use `task.apply()` (not `.delay()`) with `CELERY_TASK_ALWAYS_EAGER = True` for the narrow case of verifying the task routes to `asyncio.run()`. Do not test retry/failure-callback behavior via Celery machinery — the existing codebase explicitly sets `task_max_retries=0` and has no retry decorators, so there is no Celery retry behavior to test. The "retry logic" mentioned in PROJECT.md refers to the LLM retry inside `llm_retry()`, not Celery-level retry.

**What to mock in `_run_pipeline_async` unit tests:**
- `AsyncPostgresSaver.from_conn_string` → context manager mock returning a fake checkpointer
- `create_async_engine` → mock returning a fake engine with `dispose()`
- `db.get(Session, ...)` → return a fake Session row
- `db.get(UserProfile, ...)` → return a fake UserProfile
- `db.get(KitchenConfig, ...)` → return a fake KitchenConfig
- `db.execute(select(Equipment)...)` → return empty scalars
- `build_session_initial_state` → return `(thread_id, fake_initial_state)`
- `graph.ainvoke` → return a fake final_state OR raise an exception for failure path
- `finalise_session` → AsyncMock, assert called with correct args

The `finalise_session` mock is the primary assertion target: verify it is called exactly once in every code path (success, ValidationError, generic Exception).

---

### 3. SELECT FOR UPDATE in finalise_session()

**The race condition:**

```python
# app/core/status.py lines 47–56 (current, racy)
result = await db.get(Session, session_id)   # SELECT by PK — no lock
if not result:
    return
await db.refresh(result)                      # Re-read, still no lock
if result.status == SessionStatus.CANCELLED:  # Check happens after two unlocked reads
    return
```

Between `db.get()` and `db.refresh()`, another writer (a second Celery worker processing the same session via manual retry, or a cancellation route) can change `status` to `CANCELLED` and commit. The refresh catches it, but there is still a window between the `refresh()` return and the terminal write at the end of the function.

**The fix — SQLAlchemy async `with_for_update()`:**

SQLModel's `AsyncSession` inherits from SQLAlchemy's `AsyncSession`. SQLAlchemy supports `with_for_update()` on `select()` statements. The `db.get()` shortcut does not accept locking hints, so replace it with an explicit `select()`:

```python
from sqlmodel import select

# Replace the racy db.get() + db.refresh() with a single locked read
stmt = select(Session).where(Session.session_id == session_id).with_for_update()
result_rows = await db.exec(stmt)
result = result_rows.first()
if not result:
    return
if result.status == SessionStatus.CANCELLED:
    return
```

The `with_for_update()` issues `SELECT ... FOR UPDATE` in Postgres, which acquires a row-level exclusive lock. Any concurrent `finalise_session()` call will block at the `SELECT` until the first writer commits. Because `finalise_session()` always commits at the end (`await db.commit()`), the lock is released after the write. The second caller then acquires the lock, reads the now-terminal status, and exits early (status is already `COMPLETE`, `PARTIAL`, or `FAILED`).

**Compatibility with the existing async session pattern:**

- `db` is already an `AsyncSession` in all call sites (Celery task: `async with SessionLocal() as db`)
- `db.exec()` is the SQLModel-flavored async execute already used elsewhere in the codebase (`users.py` line 176)
- `with_for_update()` is a standard SQLAlchemy Core method — no new dependency
- The function signature does not change; callers (`_run_pipeline_async`) are unaffected

**Transaction boundary:** The lock is held from the `SELECT FOR UPDATE` until `db.commit()` at the end of the function. This is a short critical section (in-memory work, one `db.add()`, one `db.commit()`). Postgres row-level locks do not block other readers (MVCC), only concurrent writers to the same row. Latency impact is negligible.

**Test coverage for the fix:** Test with two concurrent `_run_pipeline_async` calls against the same session_id on the test DB. The simpler unit test: mock `db.exec()` to return a session with `status == CANCELLED` and assert `finalise_session()` returns without writing. Then mock `db.exec()` to return a normal session and assert the status write occurs.

---

### 4. Interval-Based Scheduler Replacement

**What already exists:**

`dag_merger.py` already contains `_IntervalIndex` — a bisect-backed sorted interval structure with `O(log n)` `count_overlapping()` and `min_end_after()`. This is the right data structure. The `_find_earliest_start()` function already uses `_IntervalIndex` via `resource_intervals[resource]`.

The remaining O(n²) behavior is in `_build_one_oven_conflict_metadata()` (the nested loop over `oven_intervals` at lines 435–460) and the `_find_stovetop_slot()` iteration. The main scheduling loop in `_find_earliest_start()` is already O(log n) per candidate advance thanks to `_IntervalIndex`.

**What compatibility with `tests/fixtures/schedules.py` requires:**

The fixtures in `schedules.py` contain pre-computed `ScheduledStep` objects with specific `start_at_minute` and `end_at_minute` values. These values are the direct output of `_merge_dags()` → the greedy list scheduler. Any change to slot-finding logic must produce identical start/end times for the same inputs. The scheduling priority order (`(-critical_path_length, recipe_slug, step_id)`) must not change.

**Safe replacement strategy:**

The interval-based replacement is additive, not structural. The `_find_earliest_start()` function's while-loop is already largely correct — it advances `candidate` using `index.min_end_after(candidate)` (which is O(log n)). The O(n) behavior only appears in the oven temperature conflict loop within that function (lines 607–631) which iterates `oven_intervals` linearly.

Replace the linear scan of `oven_intervals` within `_find_earliest_start()` with a bisect-based approach using a separate temperature-indexed structure, or accept that oven interval counts are small (at most 2–4 oven steps per meal) and the temperature conflict scan is already bounded in practice.

**For `_build_one_oven_conflict_metadata()`:** The nested loop is O(k²) where k = number of oven steps. For a dinner party menu, k ≤ 6 (realistically 2–3). This is not a real-world bottleneck. The O(n²) label in PROJECT.md refers to the scheduler's main loop, not this function.

**The true O(n²) risk:** If `_find_earliest_start()` is called in a tight loop inside `_merge_dags()` and each call iterates through all previously scheduled intervals linearly, that would be O(steps²). But `_IntervalIndex.count_overlapping()` is already O(log n) and `min_end_after()` is already O(log n). The existing implementation is not actually O(n²) in the main path — it was O(n²) before `_IntervalIndex` was added.

**Recommendation:** Verify the current `_find_earliest_start()` call pattern is truly O(log n) per step before investing in further optimization. If profiling shows linear scanning in the stovetop slot finder (`_find_stovetop_slot()`), replace `burner_intervals` linear scan with bisect — the `_IntervalIndex` is already available per burner. The schedule fixture outputs must not change; run `test_phase6_unit.py` and `test_oven_temp_conflict.py` after any change to confirm.

**Fixture compatibility check:** `MERGED_DAG_FULL` in `schedules.py` contains specific `start_at_minute` values. If the optimization changes when a step starts (because a different slot is found first), the fixture breaks. Any refactor must be output-neutral — same inputs → same schedule. Use the unit tests as a regression gate, not a discovery tool.

---

### 5. Build Order

**Dependency graph of the hardening items:**

```
[A] Extract MockDBSession to tests/helpers.py
        ↓
[B] Admin + health route tests (test_admin_health.py)
        (independent of C, D, E)

[C] Celery task unit tests (_run_pipeline_async directly)
        (independent of A, B, D, E)

[D] SELECT FOR UPDATE fix in finalise_session()
        ↓
[E] Unit tests covering the race fix (extend test_status_projection.py or new file)
        (E depends on D; D is safe to ship without E but E validates D)

[F] Scheduler interval analysis + targeted fix (if profiling confirms O(n²) exists)
        (fully independent — isolated to dag_merger.py internals)
        (must run test_phase6_unit.py + test_oven_temp_conflict.py as gate)
```

**Recommended phase order:**

**Phase 1 — Test infrastructure (no behavior change, zero regression risk)**
- Extract `MockDBSession` to `tests/helpers.py` (or confirm it can stay in `test_api_routes.py` and be imported directly)
- Add admin + health route tests (`tests/test_admin_health.py`)
- Add Celery `_run_pipeline_async` unit tests (`tests/test_tasks_unit.py`)
- Rationale: These are additive-only. They discover existing bugs without changing behavior. If they fail, the failures are in existing code, not the new tests.

**Phase 2 — Race condition fix (behavior change, narrow scope)**
- Replace `db.get()` + `db.refresh()` with `select(...).with_for_update()` in `finalise_session()`
- Add/extend unit tests for `finalise_session()` covering CANCELLED guard and normal write path
- Rationale: Isolated to one function in one file. The transaction boundary is tight. The fix cannot break existing tests because `finalise_session()` is called via `compiled_graph.ainvoke()` in Phase 3 tests — those tests use real Postgres (port 5433) and will exercise the new `SELECT FOR UPDATE` path.

**Phase 3 — Scheduler optimization (if confirmed necessary)**
- Profile `_find_earliest_start()` with a large input set to confirm O(n²) still exists
- If confirmed: replace linear scan in stovetop slot finder with `_IntervalIndex`
- Validate with `test_phase6_unit.py` and `test_oven_temp_conflict.py` (all 18+ tests must pass with identical schedule outputs)
- Rationale: Lowest urgency. `_IntervalIndex` already handles the main O(n²) case. Profiling may reveal this work is already done.

**Items that are truly independent (can be phased separately):**
- Admin/health tests (Phase 1) — no dependency on anything else
- Celery task tests (Phase 1) — no dependency on anything else
- Scheduler optimization (Phase 3) — no dependency on Phase 2

**Items with dependencies:**
- `SELECT FOR UPDATE` tests (Phase 2) must come after or alongside the fix — testing the old racy code has no value
- All test additions implicitly depend on the 99 existing tests remaining green

---

## Data Flow for the Race Fix

**Current (racy):**
```
Celery worker A                Celery worker B (manual retry)
    │                               │
    ├── db.get(Session, id)         │
    │   [reads status=GENERATING]   │
    │                               ├── db.get(Session, id)
    │                               │   [reads status=GENERATING]
    ├── db.refresh(result)          │
    │   [re-reads, still GENR]      │
    ├── status != CANCELLED         │
    │   → proceed to write          ├── db.refresh(result)
    │                               │   [re-reads, still GENR]
    ├── result.status = COMPLETE    ├── status != CANCELLED
    ├── db.add(result)              │   → proceed to write
    ├── db.commit()                 │
    │   [commits COMPLETE]          ├── result.status = COMPLETE (DOUBLE WRITE)
                                    ├── db.add(result)
                                    └── db.commit()
                                        [overwrites with stale data]
```

**Fixed (with SELECT FOR UPDATE):**
```
Celery worker A                Celery worker B (manual retry)
    │                               │
    ├── SELECT ... FOR UPDATE        │
    │   [acquires row lock]          │
    │                               ├── SELECT ... FOR UPDATE
    │                               │   [BLOCKS — waiting for lock]
    ├── status != CANCELLED          │
    ├── result.status = COMPLETE     │
    ├── db.add(result)               │
    ├── db.commit()                  │
    │   [releases lock]              │
                                    ├── [lock acquired]
                                    │   status == COMPLETE (terminal)
                                    └── return (no double write)
```

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Testing Celery task via broker
**What:** Running `.delay()` in tests, requiring Redis
**Why bad:** Adds infrastructure dependency; tests become environment-sensitive; does not test the async core logic any better than direct call
**Instead:** Test `_run_pipeline_async()` directly with patched internals; use `task.apply()` only for the narrow wrapper test

### Anti-Pattern 2: New MockDBSession for each test file
**What:** Copy-pasting `MockDBSession` into `test_admin_health.py`
**Why bad:** Duplicate implementations diverge; bugs fixed in one copy are missed in others
**Instead:** Extract to `tests/helpers.py` in Phase 1 and import from there

### Anti-Pattern 3: Changing `_merge_dags()` signature for scheduler optimization
**What:** Adding parameters to change slot-finding behavior
**Why bad:** Breaks the mockable seam used by `test_phase6_unit.py` which calls `_merge_dags()` directly
**Instead:** Optimize only the internal data structures; keep the function signature identical

### Anti-Pattern 4: Using `db.get()` + `db.refresh()` for write-before-check patterns
**What:** Using ORM shortcut methods when a locked read is needed before a conditional write
**Why bad:** Two-phase read without lock creates TOCTOU window
**Instead:** Use `select(...).with_for_update()` any time you read-then-conditionally-write in a concurrent context

### Anti-Pattern 5: Skipping fixture gate tests after scheduler changes
**What:** Modifying `_find_earliest_start()` and only running a subset of tests
**Why bad:** `schedules.py` fixtures encode exact start/end times; a single off-by-one in slot assignment breaks many tests silently
**Instead:** Always run full `test_phase6_unit.py` + `test_oven_temp_conflict.py` + `test_stovetop_heat_conflict.py` as the gate

---

## Scalability Considerations

| Concern | Current (small menus) | Risk at Scale |
|---------|----------------------|---------------|
| finalise_session race | Low (single Celery worker, max_retries=0) | High if manual retry is exposed via UI or max_retries becomes > 0 |
| Scheduler O(n²) | Non-issue (3–5 recipes, ~15 steps) | Moderate at 10+ recipes; `_IntervalIndex` already mitigates main loop |
| Admin route auth | Low (invite-only system) | Remains low; admin routes are internal-only |

---

## Sources

- Direct analysis of `app/core/status.py`, `app/api/routes/admin.py`, `app/api/routes/health.py`, `app/workers/tasks.py`, `app/workers/celery_app.py`, `app/graph/nodes/dag_merger.py`, `app/db/session.py`, `app/core/auth.py`, `app/core/deps.py`
- Direct analysis of `tests/conftest.py`, `tests/test_api_routes.py`, `tests/fixtures/recipes.py`, `tests/fixtures/schedules.py`
- SQLAlchemy async docs: `select(...).with_for_update()` is a standard API on `AsyncSession` (HIGH confidence)
- Celery docs: `task_max_retries=0` disables automatic retry; `task.apply()` executes synchronously for testing (HIGH confidence — confirmed in `celery_app.py`)
