# Testing Patterns

**Analysis Date:** 2026-04-08

## Test Framework

**Runner**: pytest 7.x+
- Config: `pytest.ini` at root
- Async support: pytest-asyncio with `asyncio_mode = auto`
- Fixture system: pytest with custom session/function scopes

**Assertion Library**: pytest built-in assertions (no extra library)

**Run Commands**:
```bash
# Run all tests except marked `integration` (fast suite, ~99 tests)
.venv/bin/python -m pytest tests/ -m "not integration" -v

# Run all tests (requires ANTHROPIC_API_KEY)
.venv/bin/python -m pytest tests/ -v

# Run specific test file
.venv/bin/python -m pytest tests/test_phase3.py -v

# Watch mode (requires pytest-watch: pip install pytest-watch)
ptw tests/ -- -m "not integration"
```

## Test Organization

### File Locations
- **Unit tests**: `tests/test_<feature>.py` — direct function calls, no database/graph
  - Example: `tests/test_phase6_unit.py` (DAG builder/merger algorithms)
  - Example: `tests/test_phase7_unit.py` (renderer timeline construction)
- **Integration tests**: `tests/test_<feature>_integration.py` — graph + database
  - Example: `tests/test_phase3.py` (full pipeline runs)
  - Example: `tests/test_generator_integration.py` (generator with real LLM calls)
- **Fixtures**: `tests/fixtures/` directory
  - `recipes.py` — hardcoded realistic fixture data (3 dishes with step IDs)
  - `schedules.py` — algorithm-verified schedule outputs
- **Helpers**: `tests/conftest.py` — shared fixtures, graph setup, mocks

### File Naming
- **Pattern**: `test_<phase_or_feature>.py` for integration tests, `test_<feature>_unit.py` for unit tests
- **Variant tests**: `test_<scenario>_<outcome>.py` when testing specific conditions (e.g., `test_oven_temp_conflict.py`, `test_stovetop_heat_conflict.py`)

### Configuration
- **pytest.ini**: Marker definitions, asyncio mode, pythonpath
- **Markers**: `@pytest.mark.integration` — only runs if ANTHROPIC_API_KEY available
  - Default run excludes: `pytest -m "not integration"`
- **Ignored test files** (in pytest.ini addopts): auth, invites, ingestion, state machine, API routes, deploy readiness (skipped for CI efficiency)

## Test Structure

### Unit Test Classes
Tests group into classes by component being tested. Each test method is a single scenario.

**Pattern from `test_phase6_unit.py`**:
```python
class TestBuildSingleDag:
    def test_happy_path_short_ribs(self):
        """Valid recipe produces correct RecipeDAG with expected edges."""
        dag = _build_single_dag(VALIDATED_SHORT_RIBS)
        
        assert dag.recipe_name == "Braised Short Ribs"
        assert dag.recipe_slug == "braised_short_ribs"
        assert len(dag.edges) == 3

    def test_cyclic_dependency_detection(self):
        """Cyclic depends_on raises ValueError."""
        with pytest.raises(ValueError, match="cycle"):
            _build_single_dag(CYCLIC_RECIPE)
```

**Test naming convention**:
- `test_<scenario>_<expected_outcome>()` — describes situation + what should happen
- Example: `test_happy_path_short_ribs`, `test_cyclic_dependency_detection`
- Example: `test_run1_happy_path_complete`, `test_run2_recoverable_error`

### Integration Test Structure
Integration tests exercise the full LangGraph pipeline with mocked LLM.

**Pattern from `test_phase3.py`**:
```python
@pytest.mark.asyncio
async def test_run1_happy_path_complete(
    compiled_graph,        # Session-scoped compiled graph with mocks
    unique_session_id,     # UUID for LangGraph thread_id
    base_initial_state,    # GRASPState dict template
    test_db_session,       # AsyncSession fresh per test
    test_user_id,          # UUID of test user
):
    """Full pipeline: all 6 mock nodes succeed."""
    config = {"configurable": {"thread_id": str(unique_session_id)}}
    initial_state = {**base_initial_state}
    
    # Create session row (required for finalise_session FK)
    session_row = Session(...)
    test_db_session.add(session_row)
    await test_db_session.commit()
    
    # Invoke pipeline
    final_state = await compiled_graph.ainvoke(initial_state, config=config)
    
    # Assert outcomes
    assert final_state.get("schedule") is not None
    assert len(final_state.get("raw_recipes", [])) == 3
```

## Fixture Architecture

### Shared Fixtures (conftest.py)

**Session-scoped** (expensive, shared across tests):
- `compiled_graph` — LangGraph graph with all mocks patched (generator, enricher, renderer)
- `test_checkpointer` — LangGraph checkpoint backend (Postgres or MemorySaver fallback)
- `test_db_engine` — SQLAlchemy async engine, creates tables once

**Function-scoped** (fresh per test):
- `test_db_session` — AsyncSession, rolls back after each test
- `test_user_id` — UUID of test user in DB
- `unique_session_id` — UUID for LangGraph thread_id (ensures checkpoint isolation)
- `base_initial_state` — GRASPState dict template (not modified during test)

**Control fixtures** (manipulate global state in compiled_graph):
- `enricher_fail_fondant()` — Makes enricher raise for Chocolate Fondant (recoverable error test)
- `enricher_return_cyclic()` — Makes enricher return cyclic step data (fatal error test)
- `generator_ft_mode()` — Makes generator return finish-together test recipes

**Pattern**:
```python
@pytest.fixture
def enricher_fail_fondant():
    """Add Fondant to skip set before test, remove after."""
    _enricher_skip_recipes.add("Chocolate Fondant")
    yield  # Test runs here
    _enricher_skip_recipes.discard("Chocolate Fondant")
```

### Recipe/Schedule Fixtures (tests/fixtures/)

**recipes.py**: Hardcoded realistic fixture data
```python
# Step ID constants (imported by both recipes.py and schedules.py)
SR_STEP_1 = "short_rib_step_1"
SR_STEP_2 = "short_rib_step_2"

# RawRecipe fixtures (generator output)
RAW_SHORT_RIBS = RawRecipe(name="Braised Short Ribs", ...)

# EnrichedRecipe fixtures (enricher output)
ENRICHED_SHORT_RIBS = EnrichedRecipe(
    source=RAW_SHORT_RIBS,
    steps=[RecipeStep(...), ...]
)

# Cyclic step data (fatal error test)
CYCLIC_STEPS_SHORT_RIBS = [RecipeStep(..., depends_on=["step_3"])]
```

**schedules.py**: Algorithm-verified schedule outputs
```python
# Verified by hand + algorithm documentation
MERGED_DAG_FULL = MergedDAG(
    scheduled_steps=[ScheduledStep(...), ...],
    resource_warnings=[...]
)
```

**Critical**: Step IDs must be globally unique and consistent
- Format: `{recipe_slug}_step_{n}` (e.g., `short_rib_step_1`)
- Defined as module-level constants in recipes.py
- Imported by schedules.py to avoid typos in depends_on references

## Mocking

### Mocking Strategy
Tests mock external APIs (Claude, Pinecone) but run real business logic (scheduling algorithms, validation).

**Patch locations** (in `conftest.py`):
```python
with patch("app.graph.nodes.generator._create_llm", return_value=gen_mock_llm):
    # Generator returns fixture recipes instead of calling Claude
    
with patch("app.graph.nodes.enricher._create_llm", return_value=enricher_mock_llm):
with patch("app.graph.nodes.enricher._retrieve_rag_context", return_value=[]):
    # Enricher returns fixture steps, skips Pinecone
    
with patch("app.graph.nodes.renderer._create_llm", return_value=renderer_mock_llm):
    # Renderer returns fixture summary
```

### Mock Implementation Pattern

**Generator mock**:
```python
gen_mock_llm = MagicMock()
gen_mock_llm.with_structured_output.return_value = gen_mock_chain

# Side effect checks _generator_ft_mode flag
async def _generator_ainvoke_side_effect(_):
    if _generator_ft_mode:
        return gen_ft_output
    return gen_default_output

gen_mock_chain.ainvoke = AsyncMock(side_effect=_generator_ainvoke_side_effect)
```

**Enricher mock**:
```python
# Inspects HumanMessage to match recipe name to fixture
async def _enricher_ainvoke_side_effect(messages):
    human_content = messages[1].content
    
    # Check control flags (per-recipe failures)
    for skip_name in _enricher_skip_recipes:
        if skip_name in human_content:
            raise Exception(f"Simulated enrichment failure for '{skip_name}'")
    
    # Return fixture based on recipe name
    for recipe_name, enriched in _enricher_fixture_map.items():
        if recipe_name in human_content:
            return StepEnrichmentOutput(...)
```

### What to Mock
- **External APIs**: LLM (Claude), Vector DB (Pinecone), Auth services
- **Network calls**: Any HTTP request outside direct control

### What NOT to Mock
- **Business logic**: DAG builder, merger, validator algorithms
- **Database operations**: Use test_db_session (real SQLAlchemy against test Postgres)
- **State transitions**: These are tested end-to-end

## Async Testing

### Pattern
All graph nodes are async. Tests use `@pytest.mark.asyncio` and `async def`.

```python
@pytest.mark.asyncio
async def test_run1_happy_path_complete(...):
    """Graph invocation is async."""
    final_state = await compiled_graph.ainvoke(initial_state, config=config)
    assert final_state is not None
```

### Fixtures
- `test_checkpointer` — async fixture (`@pytest_asyncio.fixture`)
- `test_db_engine` — async fixture
- `test_db_session` — async fixture
- Event loop provided by pytest-asyncio auto mode

## Error Testing

### Recoverable Error Test
Example: one recipe fails enrichment, pipeline continues with survivors

**From `test_phase3.py`**:
```python
@pytest.mark.asyncio
async def test_run2_recoverable_error(
    compiled_graph,
    enricher_fail_fondant,  # Fixture adds Fondant to skip set
    ...
):
    """One recipe fails enrichment (recoverable), 2-recipe schedule returned."""
    final_state = await compiled_graph.ainvoke(...)
    
    # Status is PARTIAL, not COMPLETE
    assert final_state["session_status"] == SessionStatus.PARTIAL
    
    # Error in errors list with recoverable=True
    errors = final_state.get("errors", [])
    assert any(e["error_type"] == ErrorType.ENRICHMENT_FAILURE 
               and e["recoverable"] for e in errors)
    
    # Schedule still populated (2 recipes)
    schedule = final_state.get("schedule")
    assert schedule is not None
    assert len(schedule.get("scheduled_steps", [])) > 0
```

### Fatal Error Test
Example: all recipes return cyclic dependencies, DAG builder catches cycle

**From `test_phase3.py`**:
```python
@pytest.mark.asyncio
async def test_run3_fatal_error(
    compiled_graph,
    enricher_return_cyclic,  # Fixture enables cyclic step data
    ...
):
    """Cyclic dependencies caught by DAG builder (fatal)."""
    final_state = await compiled_graph.ainvoke(...)
    
    # Status is FAILED
    assert final_state["session_status"] == SessionStatus.FAILED
    
    # No schedule
    assert final_state.get("schedule") is None
    
    # Error marked fatal (recoverable=False)
    errors = final_state.get("errors", [])
    assert any(e["error_type"] == ErrorType.DEPENDENCY_RESOLUTION
               and not e["recoverable"] for e in errors)
```

## Test Coverage

### Coverage Goals
- **Target**: 80%+ of business logic (algorithms, validation, error handling)
- **Enforcement**: Configured via coverage.py (if used; not detected in current config)
- **Acceptance**: Missing coverage is acceptable for:
  - Third-party library integrations (structlog setup, FastAPI routes)
  - Infrastructure code (database migrations, Docker setup)

### Coverage by Phase
- **Phase 3 (regression)**: 5 integration tests covering happy, recoverable, fatal, checkpoint, status paths
- **Phase 6 (algorithms)**: 18 unit tests for DAG builder and merger
- **Phase 7 (renderer)**: 21 unit tests for timeline construction and fallback summary

### Key Untested Areas
- API authentication routes (marked as skipped in pytest.ini)
- Ingestion pipeline (separate CLI flow)
- Email/notification workers

## Checkpoint Resume Testing

### Pattern
Tests LangGraph checkpoint + resume behavior without needing Redis/Celery.

**From `test_phase3.py`**:
```python
@pytest.mark.asyncio
async def test_run4_checkpoint_resume(compiled_graph, ...):
    """Interrupt at DAG builder, resume from checkpoint."""
    # First invoke — DAG builder raises due to SIMULATE_INTERRUPT env var
    os.environ["SIMULATE_INTERRUPT"] = "1"
    try:
        await compiled_graph.ainvoke(initial_state, config=config)
    except RuntimeError as e:
        assert "interrupt" in str(e).lower()
    finally:
        del os.environ["SIMULATE_INTERRUPT"]
    
    # Second invoke — same session_id, graph resumes from checkpoint
    final_state = await compiled_graph.ainvoke(initial_state, config=config)
    
    # Idempotency check: raw_recipes has 3, not 6
    assert len(final_state.get("raw_recipes", [])) == 3
```

## Test Dependencies

### Database
- **Test database**: PostgreSQL on port 5433 (separate from dev on 5432)
- **Checkpoint tables**: Created by `checkpointer.setup()` (idempotent)
- **Tables**: Created by `SQLModel.metadata.create_all()` once per session
- **Connection**: NullPool to avoid connection reuse issues

### Environment Variables
- **ANTHROPIC_API_KEY**: Required for @pytest.mark.integration tests
- **DATABASE_URL**: Uses `test_database_url` from settings.py
- **SIMULATE_INTERRUPT**: Set by checkpoint resume test to trigger failure

### Dependencies (from pyproject.toml/requirements)
- `pytest` ≥ 7.0
- `pytest-asyncio` for async/await support
- `pytest-mock` for patch/mocker fixtures (if used)
- `sqlalchemy[asyncio]` for test database
- `psycopg[binary]` for Postgres adapter
- `langgraph` and `langchain` for graph testing

## Test Run Lifecycle

### Before tests start
1. `conftest.py` loads
2. `event_loop_policy()` fixture sets up event loop (session-scoped)
3. `test_checkpointer` connects to Postgres, runs `setup()` (idempotent)
4. `test_db_engine` creates tables (idempotent)
5. `compiled_graph` builds with all mocks patched

### Per test
1. `test_db_session` creates fresh engine + session
2. `unique_session_id` generates new UUID
3. Test runs, mocks control per-recipe behavior via control fixtures
4. `test_db_session` rolls back (does not commit)

### After test completes
1. `test_db_session` closes, disposes engine
2. Control fixtures clean up global state (`_enricher_skip_recipes`, etc.)

### After all tests
1. `test_db_engine` drops all tables
2. `test_checkpointer` closes

## Debugging Tests

### Run Single Test
```bash
pytest tests/test_phase3.py::test_run1_happy_path_complete -v -s
```

### Show Print Output
```bash
pytest tests/ -s
```

### Show Variable Inspection
```bash
pytest tests/ -v --tb=short
```

### Interactive Debugging
```python
# In test, insert breakpoint
import pdb; pdb.set_trace()

# Run with -s to keep stdin
pytest tests/test_phase3.py -s -x  # -x stops at first failure
```

### Database Inspection
Test database persists between runs. To reset:
```bash
# Connect to test DB (port 5433)
psql -U postgres -h localhost -p 5433 -d grasp_test

# Drop and recreate
DROP DATABASE grasp_test;
CREATE DATABASE grasp_test;
```

---

*Testing analysis: 2026-04-08*
