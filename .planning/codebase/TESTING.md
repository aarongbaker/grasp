# Testing Patterns

**Analysis Date:** 2026-03-18

## Test Framework

**Runner:**
- pytest 8.2.2
- Config: `pytest.ini` in project root
- Async support: pytest-asyncio 0.23.7 with `asyncio_mode = auto`

**Assertion Library:**
- Standard `assert` statements; no custom assertion helpers

**Run Commands:**
```bash
pytest                          # Run all tests
pytest -m "not integration"     # Skip integration tests (skip tests requiring external API keys)
pytest tests/test_auth.py       # Run single file
pytest -v                       # Verbose output
```

## Test File Organization

**Location:**
- All tests in `tests/` directory at project root (not co-located with source)
- Mirrors backend structure loosely: `tests/test_auth.py`, `tests/test_api_routes.py`, etc.
- Fixtures in `tests/fixtures/` subdirectory

**Naming:**
- Files: `test_*.py` (e.g., `test_auth.py`, `test_phase3.py`)
- Functions: `test_*` (e.g., `test_jwt_bearer_auth_valid`, `test_empty_pages_returns_empty_list`)
- Parametrized tests use `@pytest.mark.parametrize` (not yet in codebase, but follows convention)

**Structure:**
```
tests/
├── conftest.py                  # Session-scoped fixtures (graph, checkpointer, DB)
├── fixtures/
│   ├── recipes.py              # RawRecipe, EnrichedRecipe test data
│   └── schedules.py            # Schedule/DAG test fixtures
├── test_auth.py                # JWT and auth dependency tests
├── test_api_routes.py          # HTTP-level FastAPI route tests
├── test_state_machine.py       # Pure function tests (no async/DB)
├── test_phase3.py              # LangGraph integration tests (4 test runs)
├── test_phase6_unit.py         # Validator node unit tests
└── test_phase7_unit.py         # Scheduler node unit tests
```

## Test Structure

**Suite Organization:**
```python
import pytest

def test_simple_case():
    """Test description as docstring."""
    # Arrange
    result = function_under_test(input_data)

    # Assert
    assert result == expected_value

@pytest.mark.asyncio
async def test_async_case():
    """Async test marked with decorator."""
    result = await async_function()
    assert result == expected

def test_with_mock():
    """Test using unittest.mock."""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.first.return_value = mock_user
    mock_db.exec.return_value = mock_result

    result = await function(db=mock_db)
    assert result.user_id == expected_id
```

**Patterns:**
- Tests use docstrings describing what is tested and expected outcome
- Arrange-Act-Assert pattern (implicit, not commented)
- No setup/teardown fixtures for simple tests; use pytest fixtures when state is needed
- `@pytest.mark.asyncio` for async test functions
- `@pytest.mark.integration` for tests requiring external API keys (can be skipped with `-m "not integration"`)

## Mocking

**Framework:** `unittest.mock` from Python stdlib

**Patterns:**
```python
# Async mocking
mock_db = AsyncMock()
mock_db.exec.return_value = mock_result

# Return value mocking
mock_result = MagicMock()
mock_result.first.return_value = mock_user

# Patch decorator (module-level)
@patch('core.auth.get_current_user')
def test_with_patch(mock_get_current_user):
    mock_get_current_user.return_value = test_user
    # Test code
```

**Location patterns:**
- Mocks created inline at test start
- Helper functions for creating mock objects (e.g., `_make_token()`, `_mock_user()`)
- No mock factories; inline is preferred

**What to Mock:**
- External API calls (LLM, Pinecone RAG)
- Database sessions (use `AsyncMock` for SQLAlchemy async)
- JWT token generation (use test helper `_make_token()`)

**What NOT to Mock:**
- Pydantic model validation (run real validators to catch schema bugs)
- State machine logic (run real state_machine functions)
- Error handling paths (exercise real exception types)

**Session-scoped mocks** (conftest.py):
```python
@pytest_asyncio.fixture(scope="session")
async def test_checkpointer():
    """Shared LangGraph checkpointer for all Phase 3 tests."""
    # Falls back to MemorySaver if langgraph-checkpoint-postgres not installed
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        async with AsyncPostgresSaver.from_conn_string(...) as checkpointer:
            await checkpointer.setup()
            yield checkpointer
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver
        yield MemorySaver()
```

## Fixtures and Factories

**Test Data:**
```python
# From tests/fixtures/recipes.py — fixture RawRecipe
FIXTURE_RAW_RECIPE = RawRecipe(
    name="Beef Bourguignon",
    description="Classic French braised beef stew",
    servings=4,
    cuisine="French",
    estimated_total_minutes=120,
    ingredients=[
        Ingredient(name="beef chuck", quantity="2 kg", preparation="cubed"),
    ],
    steps=["Heat oil in Dutch oven", "Sear beef on all sides"],
)
```

**Location:**
- `tests/fixtures/recipes.py`: RawRecipe, EnrichedRecipe test data
- `tests/fixtures/schedules.py`: Schedule/DAG test fixtures
- Inline fixture functions in test files for specific test suites

**Test user creation:**
```python
def _make_test_user() -> UserProfile:
    """Create a UserProfile instance (not persisted)."""
    return UserProfile(
        user_id=uuid.uuid4(),
        name="Test Chef",
        email="chef@test.com",
        dietary_defaults=["gluten-free"],
    )
```

## Coverage

**Requirements:** No target enforced; coverage not tracked in config

**View Coverage:** Not configured (no pytest-cov integration)

## Test Types

**Unit Tests:**
- Scope: Single function or class in isolation
- Examples: `test_state_machine.py` (pure function tests), `test_auth.py` (JWT logic)
- No database, no external calls
- Run in <1 second total

**Integration Tests:**
- Scope: Multiple components working together; may hit real/mock database
- Examples: `test_api_routes.py` (FastAPI routes with mock DB), `test_phase3.py` (LangGraph pipeline)
- Use fixtures (`test_db_session`, `compiled_graph`)
- Marked with `@pytest.mark.integration` if they require external API keys

**Phase-based Regression Tests (test_phase3.py):**
```python
# Four test runs covering all happy path + failure modes:
# - Run 1: Happy Path (COMPLETE) — all nodes succeed
# - Run 2: Recoverable Error (PARTIAL) — enricher drops fondant
# - Run 3: Fatal Error (FAILED) — cyclic step dependencies
# - Run 4: Checkpoint Resume (COMPLETE on 2nd invoke) — LangGraph resume

@pytest.mark.asyncio
async def test_run1_happy_path_complete(compiled_graph, unique_session_id, ...):
    """Full pipeline: all 6 mock nodes succeed."""
    config = {"configurable": {"thread_id": str(unique_session_id)}}
    final_state = await compiled_graph.ainvoke(initial_state, config=config)
    assert final_state["status"] == SessionStatus.COMPLETE
```

**E2E Tests:** Not present in codebase; Streamlit test UI (`streamlit_app.py`) used for manual testing

## Common Patterns

**Async Testing:**
```python
@pytest.mark.asyncio
async def test_async_operation():
    """Async test automatically gets event loop from event_loop fixture."""
    result = await async_function()
    assert result is not None

# Session-scoped event loop for expensive async fixtures
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
```

**Error Testing:**
```python
import pytest
from fastapi import HTTPException

@pytest.mark.asyncio
async def test_jwt_bearer_auth_expired():
    """Expired JWT token should return 401."""
    token = _make_token(str(uuid.uuid4()), expired=True)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            authorization=f"Bearer {token}",
            x_user_id=None,
            db=AsyncMock(),
        )
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()
```

**Fixture Dependency Injection:**
```python
@pytest.mark.asyncio
async def test_run1_happy_path_complete(
    compiled_graph,              # Session-scoped LangGraph instance
    unique_session_id,           # Function-scoped UUID
    base_initial_state,          # Function-scoped GRASPState dict
    test_db_session,             # Function-scoped async DB session
    test_user_id,                # Function-scoped UUID
):
    """Fixtures automatically injected by pytest."""
    final_state = await compiled_graph.ainvoke(initial_state, config=config)
```

**Conftest Architecture (conftest.py):**
- Module-level mocking control variables (e.g., `_enricher_skip_recipes`, `_enricher_cyclic_mode`)
- Session fixtures: expensive setup like graph initialization, DB connections
- Function fixtures: fresh per test (DB session, unique IDs)
- Side effects control: enricher mock respects `_enricher_skip_recipes` set for per-test failure injection

## HTTP-Level Testing (test_api_routes.py)

```python
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

def _create_test_app() -> FastAPI:
    """Create FastAPI app with routes but no lifespan."""
    app = FastAPI()
    app.include_router(health_router, prefix="/api/v1")
    # ... include other routers
    return app

@pytest.mark.asyncio
async def test_create_session_success():
    """Test /sessions POST endpoint."""
    app = _create_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/sessions",
            json={"concept": {...}, "guest_count": 4},
            headers={"X-User-ID": str(user_id)},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["session_id"] is not None
```

---

*Testing analysis: 2026-03-18*
