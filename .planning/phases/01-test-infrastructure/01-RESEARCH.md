# Phase 1: Test Infrastructure - Research

**Researched:** 2026-04-08
**Status:** Ready for planning

## Current Test Surface

- `pytest.ini` currently excludes six suites with `--ignore` flags: `tests/test_admin_invites.py`, `tests/test_invite_gating.py`, `tests/test_ingestion_tasks.py`, `tests/test_state_machine.py`, `tests/test_api_routes.py`, and `tests/test_deploy_readiness.py`.
- `tests/conftest.py` already provides the heavyweight async fixtures the phase can build on: `test_db_session`, `test_db_engine`, `_ensure_test_postgres_available()`, and the graph/checkpointer mocks.
- `tests/test_admin_invites.py` and `tests/test_invite_gating.py` duplicate admin-user and token-helper setup instead of consuming shared fixtures.
- `tests/test_api_routes.py` already mounts the `health` and `users` routers, but it does not currently assert the `/api/v1/health` contract or the equipment create/delete routes.
- `tests/test_ingestion_tasks.py` already covers `_ingest_async` with `StubDB`, `StubSessionFactory`, and stubbed ingestion modules. `_run_pipeline_async` has no dedicated unit-test file yet.
- `tests/test_phase6_unit.py` demonstrates the intended direct-call style for scheduler tests: call `_merge_dags()` with fixture DAGs and kitchen config dictionaries, no graph or DB involved.

## Code Constraints That Matter For Planning

### Admin / invite tests

- `app/api/routes/admin.py` exposes a single admin endpoint, `POST /api/v1/admin/invites`, gated by `ensure_admin_user(current_user)`.
- `app/core/auth.py` enforces JWT decoding and user lookup before route execution. Shared fixtures should keep using real access tokens; tests should not bypass auth by calling `ensure_admin_user()` directly.

### Health route

- `app/api/routes/health.py` simply executes `SELECT 1` and returns `{"status": "ok", "db": "connected"}`.
- The degraded path is an unhandled DB exception. HTTP-level testing must use `ASGITransport(..., raise_app_exceptions=False)` or equivalent so the test can assert a `500` response instead of letting the exception escape the client.

### Equipment routes

- `app/api/routes/users.py` exposes `POST /api/v1/users/{user_id}/equipment` and `DELETE /api/v1/users/{user_id}/equipment/{equipment_id}`.
- `tests/test_api_routes.py` already has a `MockDBSession`, but it currently stores rows only by a subset of primary keys and does not implement `delete()`. Equipment route tests will need that mock expanded so `Equipment` rows can be inserted, looked up by `(equipment_id, user_id)`, and removed.

### Worker task tests

- `_run_pipeline_async()` in `app/workers/tasks.py` has four planning-relevant branches:
  1. session lookup returns `None` -> return immediately
  2. user lookup returns `None` -> return immediately
  3. `build_session_initial_state()` raises `ValidationError` -> call `finalise_session()` with `FAILED` data and no schedule
  4. `graph.ainvoke()` raises -> call `finalise_session()` with an `errors[0].error_type == "unknown"` payload
- `_ingest_async()` coverage already exists in `tests/test_ingestion_tasks.py`; Phase 1 only needs that suite un-ignored and kept green.

### Scheduler regression tests

- `_merge_dags()` is the correct unit boundary for kitchen edge cases; Phase 6 tests already use it directly.
- Existing scheduler tests already prove many happy-path behaviors. Phase 1 should add the edge cases that future performance work must not regress: zero burners, missing kitchen config defaults, and malformed burner descriptor input that must not crash the scheduler.

## Risks / Collection Blockers Observed Locally

- Collecting `tests/test_admin_invites.py` outside the fully provisioned backend environment currently fails if `email_validator` is missing, because `app/api/routes/admin.py` uses `EmailStr`.
- Collecting `tests/test_ingestion_tasks.py` outside the backend environment currently fails if `celery` is missing, because `app/workers/tasks.py` imports `app.workers.celery_app` at module load.
- These are environment-precondition issues, not reasons to keep the files ignored in `pytest.ini`. The phase should assume execution inside the project test environment and keep the repo free of permanent ignore entries.

## Recommended Phase Split

### Wave 1 - shared harness and ignored-suite re-entry

- Centralize reusable auth/DB fixtures in `tests/conftest.py`
- Remove permanent ignore entries from `pytest.ini`
- Stabilize the already-written ignored suites that should now be part of normal collection

### Wave 2 - parallel feature coverage

- Route-contract coverage for `/health` and equipment CRUD
- Dedicated `_run_pipeline_async` failure-path tests plus `_ingest_async` suite stabilization
- Scheduler edge-case regression tests for Phase 4 gating

## Validation Architecture

### Quick checks

- `pytest -q tests/test_admin_invites.py tests/test_invite_gating.py tests/test_state_machine.py tests/test_deploy_readiness.py -o addopts=''`
- `pytest -q tests/test_api_routes.py -o addopts='' -k 'health or equipment'`
- `pytest -q tests/test_ingestion_tasks.py tests/test_pipeline_tasks.py -o addopts=''`
- `pytest -q tests/test_kitchen_edge_cases.py tests/test_phase6_unit.py -o addopts='' -k 'kitchen or equipment'`

### Full gate

- `pytest -m "not integration" -v`

### Sampling guidance

- Run the targeted command after each plan commit.
- Run the full non-integration suite after the final wave.

## Planning Implications

- The plan should keep all changes in test and planning artifacts; no production behavior change is required for this phase.
- Because security enforcement is enabled globally, each PLAN.md still needs a `<threat_model>` block even though the phase is test-only.
- To preserve later parallel execution, shared-fixture work must stay isolated to Wave 1. Wave 2 plans should avoid overlapping files.
