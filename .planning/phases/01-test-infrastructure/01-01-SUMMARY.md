---
phase: 01-test-infrastructure
plan: 01
subsystem: testing
tags: [pytest, fastapi, jwt, invites, auth]
requires: []
provides:
  - Shared admin/invite auth fixtures in `tests/conftest.py`
  - Re-enabled pytest discovery for the Phase 1 invite/state-machine/readiness suites
  - Stable collection path for hidden Phase 1 test files
affects: [correctness, security, performance, phase-02, phase-03, phase-04]
tech-stack:
  added: []
  patterns:
    - Shared route-contract fixtures live in `tests/conftest.py`
    - Phase-targeted suites stay discoverable by default and are selected explicitly at CLI time
key-files:
  created: []
  modified:
    - pytest.ini
    - tests/conftest.py
    - tests/test_admin_invites.py
    - tests/test_invite_gating.py
key-decisions:
  - "Centralized admin-route test users and JWT helpers in tests/conftest.py so invite suites exercise the real auth path without duplicating setup."
  - "Removed permanent pytest file ignores so critical hardening suites participate in normal collection instead of being hidden by global config."
patterns-established:
  - "Shared auth test harness: DB-backed route tests reuse persisted user fixtures plus production JWT helpers."
  - "Discovery by default: phase test files remain visible to pytest and targeted commands opt into narrower scope."
requirements-completed: [TEST-01]
duration: 7min
completed: 2026-04-08
---

# Phase 01 Plan 01: Shared Invite Harness Summary

**Shared admin invite fixtures and default pytest discovery now expose the previously hidden Phase 1 route and readiness suites without bypassing the real JWT auth path.**

## Performance

- **Duration:** 7 min
- **Started:** 2026-04-08T20:09:31Z
- **Completed:** 2026-04-08T20:16:13Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Moved reusable admin/non-admin user fixtures and real JWT token creation into `tests/conftest.py`.
- Updated both invite route suites to consume the shared harness instead of redefining local auth helpers.
- Removed permanent pytest file ignores so the invite, state-machine, and deploy-readiness suites collect in normal discovery again.

## Task Commits

Each task was committed atomically:

1. **Task 1: Extract shared admin/invite fixtures into tests/conftest.py** - `c8b52e6` (`test`)
2. **Task 2: Remove permanent ignore entries and stabilize the newly visible suites** - `adbf56d` (`test`)

**Plan metadata:** pending metadata commit for this summary/state update.

## Files Created/Modified
- `tests/conftest.py` - added shared persisted admin/non-admin fixtures, admin settings fixture, and real JWT helper for route-contract tests
- `tests/test_admin_invites.py` - switched invite issuance coverage to the shared auth fixtures
- `tests/test_invite_gating.py` - switched invite gating coverage to the shared auth fixtures
- `pytest.ini` - removed global file-level ignores that were hiding Phase 1 suites from pytest discovery

## Decisions Made
- Centralized auth-related route-test setup in `tests/conftest.py` so invite suites still use production JWT construction instead of stubbing admin checks.
- Kept suite filtering out of `pytest.ini`; targeted commands now choose scope explicitly, which keeps CI and local collection honest.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Switched verification to the project virtualenv**
- **Found during:** Task 1 (Extract shared admin/invite fixtures into tests/conftest.py)
- **Issue:** The system Python lacked `email-validator`, so importing the admin route failed during collection even though the repo virtualenv had the expected dependency set.
- **Fix:** Ran all verification with `./.venv/bin/pytest` so collection used the project environment.
- **Files modified:** None
- **Verification:** `./.venv/bin/pytest -q tests/test_admin_invites.py tests/test_invite_gating.py tests/test_state_machine.py tests/test_deploy_readiness.py -o addopts=''`
- **Committed in:** not applicable (execution-environment fix only)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** No scope creep. The deviation only corrected the verification environment so the planned test work could be validated reliably.

## Issues Encountered
None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Wave 1 is complete; the remaining Phase 1 plans can rely on the shared route-test auth harness and default pytest collection.
- DB-backed invite suites still skip when the local `grasp_test` Postgres is unavailable, but they now collect normally and no longer require global ignore rules.

## Self-Check: PASSED

---
*Phase: 01-test-infrastructure*
*Completed: 2026-04-08*
