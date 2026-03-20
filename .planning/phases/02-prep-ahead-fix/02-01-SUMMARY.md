---
phase: 02-prep-ahead-fix
plan: 01
subsystem: api
tags: [langgraph, enricher, renderer, scheduling, prep-ahead]

# Dependency graph
requires:
  - phase: 01-gantt-fix
    provides: working Gantt chart with prep-ahead filtering at ScheduleTimeline handoff
provides:
  - Tightened enricher prompt restricting can_be_done_ahead to long-lead tasks only
  - Renderer time-gate: is_prep_ahead uses hours/days window check, not raw boolean
  - Test fixtures mirror time-gate logic for deterministic assertion
affects: [renderer, enricher, scheduling fixtures, frontend schedule display]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "_is_meaningful_prep_ahead() helper pattern: separate time-gate function for prep-ahead classification used in both renderer and test fixtures"
    - "_build_timeline() returns (day_of, prep_ahead) tuple — split at construction time, not at consumer"

key-files:
  created: []
  modified:
    - graph/nodes/enricher.py
    - graph/nodes/renderer.py
    - tests/fixtures/schedules.py

key-decisions:
  - "Enricher prompt explicitly denies quick prep tasks (herb rubs, chopping, toasting, vinaigrette) even if they technically could be done ahead"
  - "Time-gate requires prep_ahead_window to contain 'hour', 'day', or 'week' — null or minute-only windows yield is_prep_ahead=False"
  - "_build_timeline() refactored to return split tuple (day_of, prep_ahead) and assign label='Prep' to prep-ahead entries directly"

patterns-established:
  - "Time-gate pattern: boolean field + string window field → composite classification via helper"

requirements-completed: [PREP-01, PREP-02, PREP-03]

# Metrics
duration: 12min
completed: 2026-03-19
---

# Phase 02 Plan 01: Tighten Prep-Ahead Classification Summary

**Enricher prompt now restricts can_be_done_ahead to long-lead tasks (brining, marinating, stock-making, braising) with a renderer time-gate requiring hours/days window — quick prep tasks no longer pollute the prep-ahead section**

## Performance

- **Duration:** 12 min
- **Started:** 2026-03-19T00:00:00Z
- **Completed:** 2026-03-19T00:12:00Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Replaced vague enricher PREP-AHEAD IDENTIFICATION with explicit allow-list (brining 4+h, marinating 4+h, stock, dough proofing, gelatin setting, curing, day-ahead braising) and explicit deny-list (herb rubs, chopping, toasting, vinaigrette, blanching, tempering chocolate)
- Added `_is_meaningful_prep_ahead()` helper in renderer.py: time-gate that requires `prep_ahead_window` to contain "hour", "day", or "week"
- Refactored `_build_timeline()` to return `(day_of, prep_ahead)` tuple with "Prep" label assigned internally
- Updated test fixtures in schedules.py to mirror the same time-gate logic — 138 tests all pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Tighten enricher prompt and add renderer time-gate** - `fbe231f` (fix)
2. **Task 2: Update test fixtures and verify** - `bfea30f` (test)

**Plan metadata:** (docs commit follows)

## Files Created/Modified
- `graph/nodes/enricher.py` - Replaced 4-line vague PREP-AHEAD IDENTIFICATION with 16-line explicit allow/deny list
- `graph/nodes/renderer.py` - Added `_is_meaningful_prep_ahead()` helper; refactored `_build_timeline()` to return split tuple; updated caller in `schedule_renderer_node`
- `tests/fixtures/schedules.py` - Added `_is_meaningful_prep_ahead()` mirror; updated `_make_timeline_entry` and `_split_timeline`

## Decisions Made
- Time-gate uses string contains check ("hour"/"day"/"week") rather than regex parsing — simpler and handles natural language windows like "up to 24 hours" correctly
- `_build_timeline()` now returns `(day_of, prep_ahead)` tuple instead of flat list — moves split responsibility into the function itself, keeping callers clean
- Prep-ahead label assignment ("Prep") moved into `_build_timeline()` rather than delegated to callers

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _build_timeline() returned flat list but tests expected (day_of, prep_ahead) tuple**
- **Found during:** Task 2 (Update test fixtures and verify)
- **Issue:** Running the test suite revealed 5 failures in TestBuildTimeline — tests called `day_of, prep_ahead = _build_timeline(...)` but the function returned `list[TimelineEntry]`. The tests also expected label="Prep" on prep-ahead entries, which wasn't being set.
- **Fix:** Refactored `_build_timeline()` to return `tuple[list[TimelineEntry], list[TimelineEntry]]`, assign label="Prep" within the function, and updated the `schedule_renderer_node` caller to unpack the tuple
- **Files modified:** `graph/nodes/renderer.py`
- **Verification:** All 138 tests pass
- **Committed in:** `bfea30f` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Auto-fix was necessary — tests were written to the correct interface, implementation lagged. No scope creep.

## Issues Encountered
None beyond the auto-fixed bug above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Prep-ahead classification is now correctly restrictive — only genuine long-lead tasks will appear in the prep-ahead section
- Phase 02 complete; no further phases planned
- If LLM still occasionally marks quick tasks as can_be_done_ahead, the time-gate in renderer ensures they won't surface in the prep-ahead UI section

---
*Phase: 02-prep-ahead-fix*
*Completed: 2026-03-19*
