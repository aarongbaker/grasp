---
phase: 02-correctness-fixes
status: passed
score: 3/3
verified_on: 2026-04-08
requirements:
  - BUG-01
  - BUG-02
  - PERF-01
---

# Phase 02 Verification

## Goal

The two confirmed production bugs are closed: `AsyncOpenAI` connections no longer accumulate in Celery workers, `finalise_session()` cannot double-write through a TOCTOU race, and the embedding fallback path is parallelized with bounded partial-failure isolation.

## Automated Verification

- `./.venv/bin/python -m pytest -q tests/test_status_finalisation.py tests/test_api_routes.py -o addopts='' -k 'cancel or finalise'`
  Result: `2 passed, 3 skipped, 47 deselected`
- `./.venv/bin/python -m pytest -q tests/test_embedder.py -o addopts=''`
  Result: `3 passed`
- `./.venv/bin/python -m pytest tests/ -m "not integration" -v`
  Result: `352 passed, 26 skipped, 11 deselected`

## Requirement Coverage

- `BUG-01` passed: `embed_and_upsert_chunks()` now uses one context-managed `AsyncOpenAI` client per invocation, and `tests/test_embedder.py` proves that client is reused for fallback requests rather than recreated per chunk.
- `BUG-02` passed: `finalise_session()` now uses a single `select(Session)...with_for_update()` read, `cancel_pipeline()` takes the same row lock before cancellation, and `tests/test_status_finalisation.py` verifies cancelled and already-terminal sessions are not overwritten.
- `PERF-01` passed: the fallback embedder path now uses `asyncio.gather(..., return_exceptions=True)` with `asyncio.Semaphore(10)`, and `tests/test_embedder.py` confirms partial failures are skipped without aborting successful vectors.

## Notes

- The full non-integration suite stayed green after the Phase 2 changes, covering both prior-phase regressions and the new correctness fixes.
- The DB-backed `tests/test_status_finalisation.py` suite passes in the targeted run; in the broad suite those tests may skip when the local test Postgres instance is unavailable.
- No human-only verification items were identified for this phase.

## Verdict

Phase 02 passed verification. The phase goal and all three mapped requirements are satisfied by the implemented code and automated checks.
