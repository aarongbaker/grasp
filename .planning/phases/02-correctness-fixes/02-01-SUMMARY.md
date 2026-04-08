---
phase: 02-correctness-fixes
plan: 01
subsystem: api
tags: [sqlalchemy, session-status, locking, fastapi, pytest]
requires:
  - phase: 01
    provides: route and worker regression coverage that keeps status changes observable under pytest
provides:
  - Lock-aware terminal session finalisation
  - Lock-aware session cancellation semantics
  - Targeted regression coverage for cancelled and terminal-session preservation
affects: [phase-02, session-status, cancellation, api-contracts]
tech-stack:
  added: []
  patterns:
    - Lock-sensitive session writes now use a single `select(...).with_for_update()` read with `populate_existing=True`
    - Route-level cancellation tests extend the existing mock DB harness instead of introducing a second app fixture path
key-files:
  created:
    - tests/test_status_finalisation.py
  modified:
    - app/core/status.py
    - app/api/routes/sessions.py
    - tests/test_api_routes.py
key-decisions:
  - "Released the `FOR UPDATE` lock with `db.rollback()` on early CANCELLED / terminal exits so lock-only reads do not linger in the session transaction."
  - "Normalized conflict response text to `session_status.value` so the API returns `complete`/`cancelled` rather than enum repr strings."
patterns-established:
  - "Session finalization and cancellation must share the same row-locking contract to preserve terminal-state ownership."
  - "Focused route-contract regressions can reuse `MockDBSession.exec()` by teaching it the exact SQLModel `select()` shape a route now issues."
requirements-completed: [BUG-02]
duration: 6min
completed: 2026-04-08
---

# Phase 02 Plan 01: Session Locking Summary

**Session finalisation and user-triggered cancellation now serialize through the same row-locking path, with regression tests proving that cancelled or already-terminal sessions are no longer overwritten.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-08T20:51:56Z
- **Completed:** 2026-04-08T20:58:06Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Replaced the non-locking `db.get()`/`refresh()` finalisation flow with a single `select(...).with_for_update()` read in `app/core/status.py`.
- Applied the same row-locking approach to `cancel_pipeline()` so cancellation and finalisation share deterministic write ordering.
- Added targeted regression coverage for cancelled rows, terminal-session no-op behavior, and HTTP-level cancel route responses.

## Task Commits

Each task was committed atomically:

1. **Task 1: Lock finalise_session() to a single row-read path** - `71cf669` (`fix`)
2. **Task 2: Apply the same row lock to cancel_pipeline() and pin the route contract** - `71cf669` (`fix`, combined with Task 1 because both tasks shared the same regression file and locking surface)

**Plan metadata:** pending metadata commit for this summary/state update.

## Files Created/Modified
- `app/core/status.py` - replaces the stale two-step finalisation read with a locking query and releases the lock on CANCELLED early exits
- `app/api/routes/sessions.py` - locks session rows before cancellation checks and returns status strings from enum `.value`
- `tests/test_status_finalisation.py` - adds focused DB-backed regressions for finalisation persistence and terminal cancel no-ops
- `tests/test_api_routes.py` - extends the route mock session to support lock-aware `select()` queries and adds cancel route contract coverage

## Decisions Made
- Used `execution_options(populate_existing=True)` on the locking session queries so a previously loaded ORM instance cannot bypass the fresh locked row state.
- Rolled back the session on early lock-only exits (`CANCELLED`, unauthorized, terminal no-op) to release the `FOR UPDATE` lock immediately rather than waiting for session teardown.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- The first cancel-route assertion exposed that the API was formatting terminal conflict details as `SessionStatus.COMPLETE` rather than `complete`. The route was updated to use `session_status.value`, and the targeted verification passed on rerun.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- The terminal session ownership contract is now protected on both the finalizer and cancel route sides.
- Plan `02-02` can proceed on top of this locking baseline without reopening the cancellation race.

## Self-Check: PASSED

---
*Phase: 02-correctness-fixes*
*Completed: 2026-04-08*
