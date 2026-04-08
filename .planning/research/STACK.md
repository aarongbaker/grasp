# Technology Stack — Hardening Research

**Project:** grasp (backend hardening milestone)
**Researched:** 2026-04-08
**Scope:** Gap-closing tools and techniques for five specific hardening areas

---

## Context

The grasp backend (Python 3.12, FastAPI 0.111.0, SQLModel 0.0.19, asyncpg 0.29.0, Celery 5.4.0, slowapi 0.1.9, pytest-asyncio 0.23.7, httpx 0.27.0) is fully functional. This research covers five specific hardening gaps — not the base stack.

Critically: the existing test suite already demonstrates working patterns. `tests/test_admin_invites.py` and `tests/test_api_routes.py` show the established async route testing approach. `tests/test_ingestion_tasks.py` shows the established Celery testing approach. New tests should follow those patterns or explain why they deviate.

---

## Area 1: Testing FastAPI Route Handlers with httpx + pytest-asyncio

**Confidence: HIGH** (patterns verified directly in codebase; httpx 0.27 + pytest-asyncio 0.23 already installed and in use)

### Recommended Pattern

The codebase already uses the correct modern approach in `tests/test_admin_invites.py` and `tests/test_api_routes.py`. New admin route and health endpoint tests should match exactly.

```python
# Pattern for routes that need real DB (admin invites, health check)
@pytest.mark.asyncio
async def test_admin_route(test_db_session):
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/v1")

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/admin/invites",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": "guest@example.com"},
        )
    assert response.status_code == 201

# Pattern for routes that only need mock DB (health check)
@pytest.mark.asyncio
async def test_health_check_db_connected():
    app = FastAPI()
    app.include_router(health_router, prefix="/api/v1")

    mock_db = AsyncMock()  # AsyncMock for execute() — health only calls execute(text("SELECT 1"))
    mock_db.execute = AsyncMock(return_value=None)

    async def override_db():
        yield mock_db

    app.dependency_overrides[get_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "connected"}
```

### Key decisions

**Use `ASGITransport(app=app)` not `app=app` directly.** httpx 0.20+ deprecated passing `app=` directly to `AsyncClient`. The codebase correctly uses `ASGITransport` already — do not regress this.

**Do not use `TestClient` for these tests.** `TestClient` is synchronous and works by running the async application in a thread via `anyio.from_thread`. When the test function is itself `async def`, using `TestClient` causes event loop conflicts. All async tests in this codebase correctly use `AsyncClient`.

**Do not mock the DB for admin route tests.** The admin invite test (`test_admin_invites.py`) uses a real `test_db_session` because the route commits to the DB and the test asserts the row exists. This is intentional — mocking the DB would make the test circular. Use `_ensure_test_postgres_available()` to skip gracefully when test Postgres is absent.

**Health check can use a mock DB.** `GET /health` only calls `db.execute(text("SELECT 1"))`. A mock is sufficient and avoids the Postgres requirement. Test both the success case (mock execute returns normally) and a failure case (mock execute raises — assert 500 or let the exception propagate to test error handling).

**`asyncio_mode = auto` is already set in `pytest.ini`.** No per-test `@pytest.mark.asyncio` is strictly required, but the existing tests use explicit marks for clarity — follow that convention.

**For admin tests, auth requires real JWT tokens.** The `_build_access_token` function from `app.api.routes.auth` is importable and used in `test_admin_invites.py`. Use `Settings(admin_email=user.email)` + `pytest.MonkeyPatch` to control the admin email setting per-test — this is the established pattern.

**Limiter must be stubbed on the app instance.** The `sessions.py` router creates its own `Limiter` instance at import time; the main app also creates one. When building a test app without the real lifespan, set `app.state.limiter = MagicMock()` to avoid `AttributeError` on rate-limit decorator lookups.

**Tests in `addopts --ignore` list still run when explicitly invoked.** `pytest.ini` ignores `test_admin_invites.py` from the default suite but it can be run directly. New admin/health tests should either be added to an existing non-ignored file or the `--ignore` list should be removed for those files.

### What to test for admin routes

| Test | Approach | DB |
|------|----------|----|
| `POST /api/v1/admin/invites` — admin can create invite | Real `test_db_session`, real JWT | Real |
| `POST /api/v1/admin/invites` — non-admin gets 403 | Real `test_db_session`, real JWT | Real |
| `POST /api/v1/admin/invites` — unauthenticated gets 401 | No auth header | Real or mock |
| `GET /api/v1/health` — returns 200 + db connected | Mock DB execute | Mock |
| `GET /api/v1/health` — DB failure surfaces cleanly | Mock DB execute raises | Mock |

---

## Area 2: Testing Celery Tasks Without a Real Broker

**Confidence: HIGH** (codebase already has established pattern in `tests/test_ingestion_tasks.py`; verified against Celery 5.4 API)

### Recommended Pattern

**Do not use `task_always_eager`.** This setting was deprecated in Celery 4.0 and removed in Celery 5.0. It does not exist in Celery 5.4. Using it will silently do nothing or raise `AttributeError`.

**Do not use `celery.conf.update(CELERY_ALWAYS_EAGER=True)`.** Same reason — the old uppercase config key is gone.

**The established pattern is: test the async inner function directly, not the Celery task wrapper.**

The Celery tasks in `app/workers/tasks.py` follow a consistent structure:
- `run_grasp_pipeline(session_id, user_id)` — sync task that calls `asyncio.run(_run_pipeline_async(...))`
- `ingest_cookbook(job_id, user_id, ...)` — sync task that calls `asyncio.run(_ingest_async(...))`

The inner async functions (`_run_pipeline_async`, `_ingest_async`) contain all the business logic. Test those directly as async functions, patching out the infrastructure they depend on (engine, sessionmaker, checkpointer, LangGraph graph).

```python
# tests/test_tasks.py — testing _run_pipeline_async directly
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.tasks import _run_pipeline_async


class StubDB:
    """Minimal async DB stub — same pattern as test_ingestion_tasks.py."""
    def __init__(self):
        self._store = {}

    async def get(self, model_class, pk):
        return self._store.get((model_class, pk))

    def add(self, obj): pass
    async def commit(self): pass
    async def flush(self): pass
    async def refresh(self, obj): pass

    async def execute(self, stmt):
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result


class StubSessionContext:
    def __init__(self, db): self.db = db
    async def __aenter__(self): return self.db
    async def __aexit__(self, *_): pass


class StubSessionFactory:
    def __init__(self, db): self.db = db
    def __call__(self, *args, **kwargs): return StubSessionContext(self.db)


class StubEngine:
    async def dispose(self): pass


class StubCheckpointer:
    async def setup(self): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass


@pytest.mark.asyncio
async def test_run_pipeline_returns_early_if_session_missing():
    """Task exits cleanly when the session row does not exist."""
    db = StubDB()  # empty store — get() returns None for any model

    with (
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=StubEngine()),
        patch("sqlalchemy.orm.sessionmaker", return_value=StubSessionFactory(db)),
        patch(
            "langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.from_conn_string",
            return_value=StubCheckpointer(),
        ),
    ):
        # Should not raise — just return early
        await _run_pipeline_async("00000000-0000-0000-0000-000000000001", str(uuid.uuid4()))
```

**Testing retry logic.** The current `celery_app.conf` sets `task_max_retries=0` — no automatic retries. There is no retry logic to test in the Celery layer. If retry logic is added later (e.g., `@celery_app.task(bind=True, max_retries=3)`), test it by calling the bound task's `retry()` method on a mock task instance:

```python
# If retry logic is ever added:
from celery.exceptions import Retry

def test_task_retries_on_transient_error(celery_app):
    """Verify the task raises Retry on a recoverable error."""
    # celery_app fixture from pytest-celery OR use unittest.mock
    task = run_grasp_pipeline  # the Celery task object
    mock_self = MagicMock()
    mock_self.request.retries = 0
    mock_self.max_retries = 3

    with patch.object(task, "retry", side_effect=Retry()) as mock_retry:
        with pytest.raises(Retry):
            # call the task body as if bound
            task(mock_self, "session-id", "user-id")
        mock_retry.assert_called_once()
```

**Testing failure callbacks.** The `ingest_cookbook` and `run_grasp_pipeline` tasks have no `on_failure` callback registered. The `_ingest_async` failure path is tested in `test_ingestion_tasks.py` by injecting a `StubDB` with `fail_commit_after`. For `_run_pipeline_async`, test the exception catch block (lines 110–123 of `tasks.py`) by making the `graph.ainvoke` mock raise:

```python
@pytest.mark.asyncio
async def test_run_pipeline_handles_graph_exception():
    """Unhandled graph exception writes FAILED status via finalise_session."""
    from app.models.session import Session
    from app.models.user import UserProfile, KitchenConfig

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    session = Session(session_id=session_id, user_id=user_id, concept_json={...})
    user = UserProfile(user_id=user_id, rag_owner_key="rk_test")
    kitchen = None

    db = StubDB()
    db._store[(Session, session_id)] = session
    db._store[(UserProfile, user_id)] = user

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("GPU OOM"))

    finalise_calls = []
    async def mock_finalise(sid, state, db_session):
        finalise_calls.append((sid, state))

    with (
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=StubEngine()),
        patch("sqlalchemy.orm.sessionmaker", return_value=StubSessionFactory(db)),
        patch("langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.from_conn_string", return_value=StubCheckpointer()),
        patch("app.graph.graph.build_grasp_graph", return_value=mock_graph),
        patch("app.core.status.finalise_session", side_effect=mock_finalise),
    ):
        await _run_pipeline_async(str(session_id), str(user_id))

    assert len(finalise_calls) == 1
    errors = finalise_calls[0][1].get("errors", [])
    assert any("GPU OOM" in e.get("message", "") for e in errors)
```

**Testing timeout handling.** `task_soft_time_limit` is set in Celery config. In production, this sends `SoftTimeLimitExceeded` to the running task. To test timeout response without Celery infrastructure, simulate it: call `_run_pipeline_async` with a patched `graph.ainvoke` that raises `celery.exceptions.SoftTimeLimitExceeded`. Verify the task catches it (or lets it propagate — document the expected behavior first since the current code does not explicitly handle it).

---

## Area 3: Fixing AsyncOpenAI Client Resource Leak

**Confidence: HIGH** (OpenAI Python SDK async context manager is documented; the bug and fix are unambiguous from reading the code)

### The Bug

`app/ingestion/embedder.py` line 72:

```python
openai_client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0)
```

The client is created but never closed. `AsyncOpenAI` holds an `httpx.AsyncClient` internally. Without explicit closure, the underlying connection pool is not released until the process exits. In a Celery worker that stays alive across tasks, this accumulates leaked connections.

### Fix

Use `AsyncOpenAI` as an async context manager:

```python
async def embed_and_upsert_chunks(chunks, book_id, user_id, db) -> int:
    async with AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0) as openai_client:
        # all embedding calls inside this block
        ...
```

`AsyncOpenAI` implements `__aenter__`/`__aexit__` which call `await self.close()` on exit. This properly drains the connection pool and releases socket descriptors.

**Why not `openai_client.close()` in a `finally` block?** The context manager approach is preferred because it handles exceptions cleanly and is idiomatic. A `try/finally` block works but is more verbose and easy to forget on refactor. The context manager makes the lifetime explicit.

**Scope consideration.** The fix wraps the entire `embed_and_upsert_chunks` function body, not individual `create()` calls. This is correct — creating one client per function call is the right scope for a Celery task (tasks are short-lived; the client lifetime should match the task lifetime, not the call-by-call loop).

**Impact on fallback loop.** The per-chunk fallback at lines 103–112 already `await`s `openai_client.embeddings.create()` inside the batch loop. The fix does not change the fallback behavior — the client is still available throughout because the `async with` wraps the entire function.

### Verification in Tests

The resource leak is not testable in the fast suite (no real OpenAI key), but it can be validated by mocking:

```python
@pytest.mark.asyncio
async def test_embedder_closes_client_on_success():
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.embeddings.create = AsyncMock(return_value=...)

    with patch("app.ingestion.embedder.AsyncOpenAI", return_value=mock_client):
        await embed_and_upsert_chunks(...)

    mock_client.__aexit__.assert_called_once()
```

---

## Area 4: Rate Limiting with slowapi on POST /sessions

**Confidence: HIGH** (slowapi 0.1.9 is already installed; `app/main.py` and `app/api/routes/sessions.py` already set up the Limiter; pattern verified from existing code)

### Current State

The infrastructure is already in place:

- `app/main.py` creates `limiter = Limiter(key_func=get_remote_address, storage_uri=redis_url)` and attaches it to `app.state.limiter`
- `app/main.py` registers `@app.exception_handler(RateLimitExceeded)` returning a 429 JSON response
- `app/api/routes/sessions.py` already imports `Limiter` and creates its own local `limiter = Limiter(key_func=get_remote_address)` — but this local limiter is not the one attached to `app.state`

### The Problem

slowapi requires the limiter used in the decorator to be the same instance as `app.state.limiter`. The sessions router creates its own unregistered `Limiter` instance. Rate limit decorators using this local instance will never trigger the registered exception handler.

### Fix

**Do not use a local limiter in the router.** Use a module-level limiter in the router that matches how other slowapi decorators work with the registered app limiter. There are two valid approaches:

**Option A (recommended): Use `request.app.state.limiter` via a shared Limiter singleton**

```python
# app/api/routes/sessions.py
from slowapi import Limiter
from slowapi.util import get_remote_address

# This single instance must be registered with app.state.limiter at startup
limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/sessions")

@router.post("", status_code=201)
@limiter.limit("5/minute")  # 5 session creations per minute per IP
async def create_session(request: Request, body: CreateSessionRequest, ...):
    ...
```

Then in `app/main.py`, replace the existing `app.state.limiter = limiter` assignment so the sessions router's `limiter` instance IS the one registered:

```python
# app/main.py — import the limiter from sessions router, not create a new one
from app.api.routes.sessions import limiter as sessions_limiter
app.state.limiter = sessions_limiter
```

However, this creates a circular import problem given the current structure.

**Option B (recommended, avoids circular import): Shared limiter module**

```python
# app/core/limiter.py — new file, no circular imports
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
```

```python
# app/main.py
from app.core.limiter import limiter
# ... Redis check still applies:
if _redis_is_reachable(...):
    limiter.storage_uri = _settings.redis_url  # or reinitialize with storage_uri
app.state.limiter = limiter
```

```python
# app/api/routes/sessions.py
from app.core.limiter import limiter
from fastapi import Request

@router.post("", status_code=201)
@limiter.limit("5/minute")
async def create_session(request: Request, body: CreateSessionRequest, ...):
    ...
```

**`request: Request` parameter is mandatory.** slowapi injects rate-limit information via the `Request` object. Any route using `@limiter.limit(...)` must accept a `request: Request` parameter even if the route doesn't use it directly. The decorator reads `request.app.state.limiter` to validate it's the same instance.

**Key function.** `get_remote_address` uses the `X-Forwarded-For` header if present, falling back to the direct IP. This is correct for a reverse-proxied deployment (Railway, Fly.io). If the deployment sets `X-Forwarded-For` to include internal IPs, consider using `get_ipaddr` from slowapi.util instead.

**Rate limit string format.** slowapi uses limits-style strings: `"5/minute"`, `"100/hour"`, `"10/second"`. Multiple limits can be chained: `@limiter.limit("5/minute;100/hour")`.

**Redis vs in-memory.** The existing `_redis_is_reachable()` check in `main.py` correctly handles the fallback. In-memory limits are per-worker — with multiple Celery workers or API instances, limits are not shared. This is acceptable for the current single-worker deployment but note it in the implementation.

### Testing Rate Limits

```python
@pytest.mark.asyncio
async def test_rate_limit_on_post_sessions():
    """6th request in a minute should get 429."""
    from app.core.limiter import limiter

    app = FastAPI()
    app.include_router(sessions_router, prefix="/api/v1")
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _handler(request, exc):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        for _ in range(5):
            response = await ac.post("/api/v1/sessions", json={...}, headers={"Authorization": "Bearer ..."})
            assert response.status_code in (201, 400)  # valid or validation error, not 429

        # 6th request should be throttled
        response = await ac.post("/api/v1/sessions", json={...}, headers={"Authorization": "Bearer ..."})
        assert response.status_code == 429
```

Note: in-memory limiter state persists across tests in the same process. Use `limiter._storage.reset()` or create a fresh `Limiter` instance per test to avoid cross-test contamination.

---

## Area 5: Database-Level Locking (SELECT FOR UPDATE) with SQLAlchemy AsyncSession

**Confidence: HIGH** (SQLAlchemy 2.0 async API is well-established; the bug location is clear from reading `app/core/status.py`)

### The Bug

`app/core/status.py` lines 47–55:

```python
result = await db.get(Session, session_id)  # non-locking read
if not result:
    return
await db.refresh(result)  # re-read from DB — race window here
if result.status == SessionStatus.CANCELLED:
    return
```

Between `db.refresh()` and the subsequent `await db.commit()`, another concurrent call (e.g., a cancellation request) can write `SessionStatus.CANCELLED` to the row. The finalise function then overwrites it with `COMPLETE` or `FAILED`.

### Fix

Replace the non-locking read with a `SELECT FOR UPDATE` using SQLAlchemy's `with_for_update()`:

```python
from sqlalchemy import select

async def finalise_session(session_id: uuid.UUID, final_state: dict, db: AsyncSession) -> None:
    # SELECT ... FOR UPDATE — acquires row-level lock until commit
    stmt = (
        select(Session)
        .where(Session.session_id == session_id)
        .with_for_update()
    )
    result = (await db.execute(stmt)).scalar_one_or_none()

    if not result:
        return

    if result.status == SessionStatus.CANCELLED:
        return

    # ... write terminal state ...
    db.add(result)
    await db.commit()  # lock released here
```

**Why `with_for_update()` on a `select()` not `db.get()`?** SQLAlchemy's `AsyncSession.get()` does not support `with_for_update`. You must use `session.execute(select(Model).where(...).with_for_update())` to get a locking read. `db.get()` always does a non-locking `SELECT`.

**Why not pessimistic locking via `db.refresh()`?** `db.refresh()` issues a plain `SELECT` — it does not lock. The current code's `await db.refresh(result)` provides no protection against the race.

**Transaction boundary.** The `SELECT FOR UPDATE` holds the lock until the session's transaction is committed or rolled back. In an `AsyncSession`, the transaction is implicit and commits at `await db.commit()`. The lock is held from the `execute(select(...).with_for_update())` call through `await db.commit()`. This is the correct scope.

**PostgreSQL behavior.** In asyncpg (the driver used here), `SELECT FOR UPDATE` in a transaction works correctly with async sessions. The lock is connection-scoped and released on commit. No additional configuration is needed.

**Impact on concurrent cancellation.** With this fix: if a cancellation writes `CANCELLED` first, the `SELECT FOR UPDATE` in `finalise_session` will wait for the cancellation transaction to commit, then read the committed `CANCELLED` status and return early. If `finalise_session` acquires the lock first, the cancellation write will block until `finalise_session` commits, at which point the status is terminal and the cancellation should check that and no-op.

The cancellation route must also acquire the lock for this to be fully race-free. The route that writes `CANCELLED` should similarly use `with_for_update()`:

```python
# In the PATCH /sessions/{id} cancellation handler
stmt = (
    select(Session)
    .where(Session.session_id == session_id)
    .with_for_update()
)
session_row = (await db.execute(stmt)).scalar_one_or_none()
if session_row and session_row.status not in (SessionStatus.COMPLETE, SessionStatus.FAILED, SessionStatus.PARTIAL):
    session_row.status = SessionStatus.CANCELLED
    db.add(session_row)
    await db.commit()
```

**Import change.** The current `status.py` uses `from app.models.session import Session` and `db.get(Session, session_id)`. The fix requires `from sqlalchemy import select` and a `select(Session).where(...)` query. SQLModel sessions support SQLAlchemy `select()` — no driver change needed.

**SQLModel note.** `AsyncSession` from `sqlmodel.ext.asyncio.session` inherits from SQLAlchemy's `AsyncSession` and supports `with_for_update()` transparently.

### Testing the Fix

```python
@pytest.mark.asyncio
async def test_finalise_session_respects_cancelled_status(test_db_session, test_user_id):
    """finalise_session does not overwrite CANCELLED with COMPLETE."""
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.CANCELLED,
        concept_json={"free_text": "test"},
    )
    test_db_session.add(session)
    await test_db_session.commit()

    final_state = {
        "schedule": {"summary": "Done", "total_duration_minutes": 60, "steps": []},
        "validated_recipes": [],
        "errors": [],
    }
    await finalise_session(session_id, final_state, test_db_session)

    await test_db_session.refresh(session)
    assert session.status == SessionStatus.CANCELLED  # must not be overwritten
```

Note: testing the race condition itself (concurrent writes) requires either threading or two separate DB connections. The unit test above validates the guard logic, not the lock acquisition. For the lock acquisition, verify manually or via a slow-query integration test.

---

## What NOT to Do

| Antipattern | Why | Instead |
|-------------|-----|---------|
| `task_always_eager = True` | Removed in Celery 5.0 | Test `_async_inner` functions directly |
| `celery_app.task(...)` mock via `monkeypatch` on the task name | Bypasses the actual function body | Import and call `_run_pipeline_async` / `_ingest_async` directly |
| `db.get(Model, pk)` with `with_for_update` | SQLAlchemy `get()` does not support locking | Use `select(Model).where(...).with_for_update()` |
| `AsyncClient(app=app)` (deprecated) | httpx 0.20+ removed the `app=` shorthand | Use `AsyncClient(transport=ASGITransport(app=app))` |
| Global `limiter = Limiter(...)` per router (current state in sessions.py) | Creates a limiter instance disconnected from `app.state.limiter` | Shared limiter module in `app/core/limiter.py` |
| `openai_client.close()` in `finally` | Works but fragile on refactor | `async with AsyncOpenAI(...) as client:` |
| Mocking the DB for admin invite tests | Makes the test circular (can't verify the row was committed) | Use real `test_db_session` with the Postgres test instance |

---

## Version Compatibility Summary

| Library | Installed | Pattern Used | Notes |
|---------|-----------|--------------|-------|
| httpx | 0.27.0 | `ASGITransport(app=app)` | Correct; `app=` shorthand removed in 0.20 |
| pytest-asyncio | 0.23.7 | `asyncio_mode = auto` + `@pytest.mark.asyncio` | Both work; existing tests use explicit marks |
| slowapi | 0.1.9 | `@limiter.limit("5/minute")` + `request: Request` | Must share limiter instance with `app.state.limiter` |
| SQLAlchemy (via SQLModel 0.0.19) | 2.x | `select(...).with_for_update()` | `AsyncSession.get()` does not support locking |
| openai | (via langchain-openai 1.1.10) | `async with AsyncOpenAI(...) as client:` | `__aenter__`/`__aexit__` close the httpx pool |
| Celery | 5.4.0 | Test `_async_inner` functions directly | `task_always_eager` removed; no retry config active |

---

*Research date: 2026-04-08*
*Confidence: HIGH across all five areas — patterns verified against installed library versions and existing codebase.*
