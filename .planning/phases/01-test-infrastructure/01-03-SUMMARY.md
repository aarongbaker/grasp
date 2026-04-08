---
phase: 01-test-infrastructure
plan: 03
subsystem: testing
tags: [pytest, celery, workers, async, finalise-session]
requires:
  - phase: 01
    provides: re-enabled pytest discovery and baseline route/test harness coverage
provides:
  - Dedicated `_run_pipeline_async` failure-path coverage
  - Early-return assertions that skip `finalise_session()` for missing session/user cases
  - Regression protection for worker failure payload shapes without a broker
affects: [phase-02, correctness, worker-reliability, session-status]
tech-stack:
  added: []
  patterns:
    - Worker task tests patch the same import points `_run_pipeline_async()` uses at runtime
    - Failure-path tests assert `finalise_session()` payload shape instead of depending on external services
key-files:
  created:
    - tests/test_pipeline_tasks.py
  modified: []
key-decisions:
  - "Stubbed the exact lazy-import module path (`app.graph.graph`) so `_run_pipeline_async()` tests exercise production import boundaries."
  - "Left `tests/test_ingestion_tasks.py` unchanged because it already passed under normal discovery once the ignore rules were removed."
patterns-established:
  - "Celery worker wrapper tests use lightweight async context-manager stubs for the checkpointer and DB session factory."
  - "Failure-path assertions verify both whether `finalise_session()` is called and the shape of the final-state payload."
requirements-completed: [TEST-03, TEST-04]
duration: 6min
completed: 2026-04-08
---

# Phase 01 Plan 03: Worker Failure Coverage Summary

**The Celery pipeline wrapper now has dedicated regression coverage for its no-op early exits and failed-session finalisation paths, while the existing ingestion task tests continue to pass under normal pytest discovery.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-08T20:21:11Z
- **Completed:** 2026-04-08T20:27:19Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- Added `_run_pipeline_async` tests for missing session, missing user, validation failure, and graph exception branches.
- Asserted that early exits do not call `finalise_session()`, while failure paths finalise with the expected error payloads.
- Confirmed the pre-existing `_ingest_async` suite still passes with the new worker-task tests under normal discovery.

## Task Commits

Each task was committed atomically:

1. **Task 1: Create dedicated _run_pipeline_async failure-path tests** - `95f63cb` (`test`)
2. **Task 2: Keep _ingest_async coverage active after ignore removal** - `95f63cb` (`test`, verification only; no source change needed)

**Plan metadata:** pending metadata commit for this summary/state update.

## Files Created/Modified
- `tests/test_pipeline_tasks.py` - added async unit coverage for worker early exits and failure finalisation payloads

## Decisions Made
- Patched the exact lazy-import module path used by `_run_pipeline_async()` (`app.graph.graph`) so the tests mirror production dependency resolution.
- Kept `tests/test_ingestion_tasks.py` untouched because its current assertions already stayed green after Phase 1 re-enabled normal collection.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- The first test harness version patched `app.graph.graph.build_grasp_graph` through the package attribute, but `_run_pipeline_async()` imports the module lazily by path. The suite was updated to stub the concrete `app.graph.graph` module in `sys.modules`, after which all targeted tests passed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 2 correctness work now has worker-level regression protection around `finalise_session()` behavior before changing task execution code.
- Only `01-04` remains for Phase 1 completion.

## Self-Check: PASSED

---
*Phase: 01-test-infrastructure*
*Completed: 2026-04-08*
