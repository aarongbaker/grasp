# Phase 1: Test Infrastructure - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Close the identified test coverage gaps from the post-Phase-7 audit. The pipeline is complete; this phase makes it defensible in CI. Deliverables:
- Un-ignore all currently excluded test files from pytest.ini and fix them to pass
- Write `test_pipeline_tasks.py` for `_run_pipeline_async` failure paths (TEST-03)
- Write equipment CRUD tests (TEST-05) — these are likely in the excluded `test_api_routes.py`
- Write kitchen config edge case tests (TEST-06) targeting `_merge_dags()` directly
- Promote shared test infrastructure into `conftest.py` so Phase 2–4 tests can build on it

No new production code is written in this phase.

</domain>

<decisions>
## Implementation Decisions

### Pytest.ini un-ignore strategy
- Remove ALL `--ignore` entries from `pytest.ini` — un-ignore every excluded file
- Fix any failures in the newly-visible tests
- `test_admin_invites.py`, `test_api_routes.py`, `test_ingestion_tasks.py` are the primary Phase 1 targets; `test_invite_gating.py`, `test_state_machine.py`, `test_deploy_readiness.py` get un-ignored too
- After this phase, `pytest -m "not integration" -v` passes 130+ tests with zero `--ignore` entries

### Celery task test isolation (_run_pipeline_async)
- New file: `tests/test_pipeline_tasks.py` (separate from `test_ingestion_tasks.py`)
- Use the StubDB pattern already established in `test_ingestion_tasks.py` — a stub class that returns controlled objects
- Three test cases required:
  1. Early-exit when session not found: assert `finalise_session` was NOT called AND function returns `None` cleanly
  2. Early-exit when user not found: same assertion pair
  3. ValidationError from `build_session_initial_state`: assert `finalise_session` IS called with `FAILED` status and no schedule
  4. Unhandled graph exception (`graph.ainvoke` raises): assert `finalise_session` called with error entry containing `"unknown"` error type

### Kitchen config edge case tests
- Test the scheduler layer via `_merge_dags()` directly — same pattern as `test_phase6_unit.py` calling `_build_single_dag()`
- Three scenarios:
  1. Zero burners (`max_burners=0`, empty `burners` list): no STOVETOP steps get scheduled (they go unscheduled or degrade)
  2. Missing config (`kitchen_config=None`/empty dict): scheduler uses defaults without crashing
  3. Invalid descriptors (malformed burner descriptor data): doesn't raise an unhandled exception
- These live in `tests/test_kitchen_edge_cases.py` — named to signal their role as Phase 4 regression gate

### Shared infrastructure extraction into conftest.py
- Promote the following from `test_admin_invites.py` into `conftest.py`:
  - `admin_user` fixture (creates configured admin UserProfile in test DB)
  - `non_admin_user` fixture (creates non-admin UserProfile in test DB)
- Add to `conftest.py`:
  - Full app factory fixture returning a FastAPI app with real routers and DB session override
  - `AsyncClient` fixture wrapping the full app (reusable for Phase 2–4 route tests)
- `test_admin_invites.py` can then import/use the promoted fixtures from conftest rather than defining its own

### Claude's Discretion
- Exact StubDB implementation in `test_pipeline_tasks.py` — whether to reuse or duplicate from `test_ingestion_tasks.py`
- How to handle `test_invite_gating.py`, `test_state_machine.py`, `test_deploy_readiness.py` if they have failures (fix in Phase 1 or mark skip with explanation)
- Whether the FastAPI app fixture in conftest.py uses the full production app or a test-scoped minimal app

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing test infrastructure (read before writing anything)
- `tests/conftest.py` — Session/function-scoped fixtures, StubDB patterns, mock architecture, event loop setup
- `tests/test_ingestion_tasks.py` — StubDB pattern to mirror for `test_pipeline_tasks.py`
- `tests/test_phase6_unit.py` — Direct function call pattern to mirror for kitchen edge case tests

### Source files under test
- `app/api/routes/admin.py` — Admin invite endpoint (`ensure_admin_user` uses `settings.admin_email`)
- `app/api/routes/health.py` — Health check endpoint (`GET /health`, executes `SELECT 1`)
- `app/workers/tasks.py` — `_run_pipeline_async` three failure paths (lines 53-125)
- `app/api/routes/users.py` — Equipment CRUD (`POST /{user_id}/equipment`, `DELETE /{user_id}/equipment/{equipment_id}`)
- `app/graph/nodes/dag_merger.py` — `_merge_dags()` scheduler function for kitchen edge case tests

### Test files to un-ignore
- `tests/test_admin_invites.py` — 3 tests, fully written, admin/non-admin/unauthenticated paths
- `tests/test_api_routes.py` — Route-level HTTP tests including health check and sessions
- `tests/test_ingestion_tasks.py` — `_ingest_async` StubDB tests (TEST-04)
- `tests/test_invite_gating.py` — Un-ignore, fix if needed
- `tests/test_state_machine.py` — Un-ignore, fix if needed
- `tests/test_deploy_readiness.py` — Un-ignore, fix if needed

### Project requirements
- `.planning/REQUIREMENTS.md` — TEST-01 through TEST-06 acceptance criteria

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `StubDB` in `tests/test_ingestion_tasks.py`: Reusable stub class with controllable commit failure — template for `test_pipeline_tasks.py` StubDB
- `_ensure_test_postgres_available()` in `conftest.py`: Test-skip helper for DB-backed tests — reuse in new test files
- `_access_token_for()` in `tests/test_admin_invites.py`: JWT builder for test callers — move to conftest or a shared helper
- `enricher_fail_fondant` / `enricher_return_cyclic` control fixtures in `conftest.py`: Pattern for per-test behavior control

### Established Patterns
- **Direct function calls for unit tests**: `_build_single_dag(recipe)` in Phase 6, `_merge_dags(dags, kitchen)` should follow the same pattern for kitchen edge cases
- **StubDB for Celery task isolation**: `test_ingestion_tasks.py` pattern — async `get()` returns controlled object, `commit()`/`rollback()` are tracked
- **Session-scoped graph, function-scoped DB session**: Don't break this — new tests should follow function-scoped DB session pattern
- **`@pytest.mark.asyncio`** with `asyncio_mode = auto` in pytest.ini — all async tests

### Integration Points
- `app/core/auth.py` `ensure_admin_user()` checks `settings.admin_email` — tests patch `get_settings` via `MonkeyPatch` (already done in `test_admin_invites.py`)
- `app/db/session.py` `get_session` — dependency override for route tests (already done in admin test fixture)
- `app/graph/nodes/dag_merger.py` imports `_merge_dags` directly for scheduler unit tests

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-test-infrastructure*
*Context gathered: 2026-04-08*
