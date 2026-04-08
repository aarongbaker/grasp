---
phase: 03-security-surface-closure
plan: 02
subsystem: api
tags: [pydantic, sqlmodel, kitchen-config, validation, pytest]
requires:
  - phase: 02
    provides: a stable backend regression baseline so stronger validation can land without obscuring correctness failures
provides:
  - Kitchen-config burner and rack ceilings at the persisted model boundary
  - Equipment count enforcement at the user route boundary
  - Route and scheduler-facing regressions for impossible kitchen structures
affects: [phase-03, users, kitchen-config, scheduler-contracts]
tech-stack:
  added: []
  patterns:
    - Patch-style kitchen updates must validate a merged candidate snapshot before mutating the persisted SQLModel row
    - Legacy second-oven defaults remain readable while explicit invalid second-oven inputs are rejected at the request boundary
key-files:
  created: []
  modified:
    - app/models/user.py
    - app/api/routes/users.py
    - tests/test_api_routes.py
    - tests/test_phase6_unit.py
key-decisions:
  - "Validated the merged kitchen snapshot inside `update_kitchen()` so lowering `max_burners` can still reject an already-oversized burner list."
  - "Rejected explicit `max_second_oven_racks` input when `has_second_oven` is false without retroactively invalidating older persisted rows that still carry the legacy default."
patterns-established:
  - "Security-sensitive patch routes should surface Pydantic validation errors through JSON-serializable `jsonable_encoder(exc.errors())` payloads."
  - "Scheduler-facing config invariants belong in model tests as well as HTTP tests so bad kitchen shapes are caught before algorithm code runs."
requirements-completed: [SEC-02]
duration: 6min
completed: 2026-04-08
---

# Phase 03 Plan 02: Kitchen Validation Summary

**Kitchen profile writes now reject out-of-policy burner, rack, and equipment inputs before they can persist, and impossible burner-cardinality combinations are pinned at both the route and scheduler-facing model seams.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-08T21:59:26Z
- **Completed:** 2026-04-08T22:05:36Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Added numeric ceilings to `KitchenConfig` so burner and rack counts are validated at the persisted model boundary.
- Enforced the `20`-item per-user equipment cap in the equipment route and added regressions for the failure path.
- Added merged-snapshot validation and unit coverage so burner descriptor lists cannot exceed `max_burners`, and explicit second-oven rack inputs are rejected when no second oven is enabled.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add numeric ceilings for kitchen and equipment inputs** - `a081005` (`fix`)
2. **Task 2: Enforce cross-field invariants without silent normalization** - `bd988de` (`test`, paired with the Task 1 route changes because the invariant implementation and route rejection landed together)

**Plan metadata:** pending metadata commit for this summary/state update.

## Files Created/Modified
- `app/models/user.py` - adds burner/rack ceilings and burner-count invariant validation
- `app/api/routes/users.py` - validates merged kitchen updates, rejects explicit invalid second-oven rack writes, and caps equipment rows at 20 per user
- `tests/test_api_routes.py` - covers kitchen ceiling failures, burner-cardinality rejection, second-oven rack rejection, and equipment cap failures
- `tests/test_phase6_unit.py` - proves scheduler-facing kitchen config validation rejects too many burner descriptors

## Decisions Made
- Kept the equipment cap in the route layer because equipment is stored as separate rows, not inside `KitchenConfig`.
- Preserved compatibility with older stored kitchen rows by rejecting new invalid second-oven writes at the request edge instead of making every historical `has_second_oven=false, max_second_oven_racks=2` payload unloadable.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The first merged-snapshot failure path surfaced a FastAPI serialization bug because raw Pydantic errors included UUID objects. The route now encodes validation details with `jsonable_encoder()` before returning HTTP 422.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Kitchen and equipment inputs now fail closed before reaching the scheduler.
- The RAG ownership/cache work can proceed without carrying unresolved input-validation ambiguity into later phases.

## Self-Check: PASSED

---
*Phase: 03-security-surface-closure*
*Completed: 2026-04-08*
