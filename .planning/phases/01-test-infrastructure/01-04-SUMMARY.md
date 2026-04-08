---
phase: 01-test-infrastructure
plan: 04
subsystem: testing
tags: [pytest, scheduler, dag-merger, kitchen-config, equipment]
requires:
  - phase: 01
    provides: scheduler/unit-test baseline and Phase 1 test discovery
provides:
  - Dedicated kitchen-edge regression suite for `_merge_dags()`
  - Graceful malformed-burner fallback in scheduler burner-slot selection
  - Regression proving missing kitchen equipment removes serialization constraints
affects: [phase-04, scheduler, kitchen-config, equipment-constraints]
tech-stack:
  added: []
  patterns:
    - Kitchen edge cases are tested directly at `_merge_dags()` rather than through graph wrappers
    - Scheduler regressions compare overlap timing, not brittle full-schedule snapshots
key-files:
  created:
    - tests/test_kitchen_edge_cases.py
  modified:
    - app/graph/nodes/dag_merger.py
    - tests/test_phase6_unit.py
key-decisions:
  - "Malformed burner descriptors now fall back to `max_burners` numbering instead of surfacing a raw validation error from `_merge_dags()`."
  - "The equipment-unlock regression compares constrained versus unconstrained overlap timing directly so later scheduler work can change unrelated schedule details safely."
patterns-established:
  - "Kitchen-config edge cases fail with typed scheduler outcomes or deterministic fallbacks, not unhandled validation exceptions."
  - "Equipment-capacity regressions assert the presence or absence of overlap constraints explicitly."
requirements-completed: [TEST-05, TEST-06]
duration: 6min
completed: 2026-04-08
---

# Phase 01 Plan 04: Scheduler Edge-Case Coverage Summary

**The scheduler now has direct kitchen-edge regression coverage, including graceful fallback for malformed burner descriptors and an explicit proof that removing tracked equipment removes the serialization constraint.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-08T20:27:19Z
- **Completed:** 2026-04-08T20:33:29Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Added a dedicated `tests/test_kitchen_edge_cases.py` suite covering zero burners, missing kitchen config, and malformed burner-descriptor input.
- Hardened `_build_burner_slots()` so malformed burner metadata degrades to stable fallback numbering instead of raising an unhandled validation error.
- Added a focused scheduler regression showing that overlap becomes legal again when the required equipment is absent from `kitchen_config`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add direct _merge_dags kitchen edge-case tests** - `013b01d` (`test`)
2. **Task 2: Lock down equipment constraint unlock behavior** - `fabda1a` (`test`)

**Plan metadata:** pending metadata commit for this summary/state update.

## Files Created/Modified
- `tests/test_kitchen_edge_cases.py` - added direct scheduler edge-case coverage for zero burners, defaults, and malformed burner descriptors
- `app/graph/nodes/dag_merger.py` - added graceful fallback when burner descriptors are invalid
- `tests/test_phase6_unit.py` - added constrained-versus-unconstrained equipment overlap regression

## Decisions Made
- Treated malformed burner descriptors as recoverable kitchen-config noise and fell back to `max_burners` numbering, which preserves scheduler behavior for bad user input.
- Kept the equipment regression scoped to start-time overlap rather than whole-schedule equality so it remains stable across future scheduler refactors.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Prevented malformed burner descriptors from crashing `_merge_dags()`**
- **Found during:** Task 1 (Add direct _merge_dags kitchen edge-case tests)
- **Issue:** Invalid burner descriptor payloads raised a raw `ValidationError` out of `_build_burner_slots()`, which violated the plan’s requirement for graceful kitchen-config handling.
- **Fix:** Catch descriptor validation failure, log it, and fall back to stable `max_burners` slot numbering.
- **Files modified:** `app/graph/nodes/dag_merger.py`
- **Verification:** `./.venv/bin/pytest -q tests/test_kitchen_edge_cases.py tests/test_phase6_unit.py -o addopts='' -k 'kitchen or equipment'`
- **Committed in:** `013b01d`

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** The auto-fix was necessary to satisfy the required graceful-degradation contract for malformed kitchen input. No scope creep beyond that hardening.

## Issues Encountered
None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 1 scheduler regression coverage is now in place for the Phase 4 performance work gate.
- All planned Phase 1 work is complete; the phase is ready for a final verification sweep.

## Self-Check: PASSED

---
*Phase: 01-test-infrastructure*
*Completed: 2026-04-08*
