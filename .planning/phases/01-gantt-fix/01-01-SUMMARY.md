---
phase: 01-gantt-fix
plan: 01
subsystem: ui
tags: [react, css-modules, gantt, typescript]

# Dependency graph
requires: []
provides:
  - CookingGantt renders only day-of steps (prep-ahead entries filtered at source and defensively in component)
  - Bars are proportionally sized and positioned using percentage-based math
  - Lane height increased to 40px with 8px minimum bar width
  - Keyboard accessibility via tabIndex and :focus-visible copper outline
affects: [02-gantt-clock-axis]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Filter is_prep_ahead at data handoff point (ScheduleTimeline) AND defensively in consumer (CookingGantt)"
    - "Percentage-based bar geometry: leftPct = time_offset_minutes / totalDurationMinutes * 100"

key-files:
  created: []
  modified:
    - frontend/src/components/session/ScheduleTimeline.tsx
    - frontend/src/components/session/CookingGantt.tsx
    - frontend/src/components/session/CookingGantt.module.css

key-decisions:
  - "Filter prep-ahead entries at the ScheduleTimeline handoff point, not inside CookingGantt — root cause fix"
  - "Add defensive filter inside CookingGantt as well to guard against future regressions"
  - "Remove hasPrepAhead memo and prep-ahead legend from Gantt — prep-ahead section in ScheduleTimeline owns that legend"

patterns-established:
  - "Dual-filter pattern: filter at data source + defensive filter in consumer for critical correctness"

requirements-completed: [GANTT-01, GANTT-02, GANTT-03, GANTT-04]

# Metrics
duration: 2min
completed: 2026-03-19
---

# Phase 01 Plan 01: Gantt Data Pipeline Fix Summary

**Root-cause fix for missing Gantt bars: filter prep-ahead entries at ScheduleTimeline handoff and defensively inside CookingGantt, with 40px lane height and keyboard focus ring**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-19T03:39:45Z
- **Completed:** 2026-03-19T03:41:54Z
- **Tasks:** 1 of 1 auto tasks
- **Files modified:** 3

## Accomplishments
- Fixed root cause: ScheduleTimeline was concatenating prep-ahead entries into the Gantt timeline, breaking bar positioning for all subsequent entries
- Added defensive filter in CookingGantt so any prep-ahead entries that slip through are excluded
- Removed hasPrepAhead memo and prep-ahead legend block from CookingGantt (prep-ahead section in ScheduleTimeline owns that UI)
- Increased lane height from 32px to 40px and minimum bar width from 4px to 8px for better readability
- Added keyboard accessibility: tabIndex={0} on each bar group, :focus-visible outline using copper accent color

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix data pipeline and bar rendering in CookingGantt** - `de40dbf` (fix)

**Plan metadata:** TBD (docs: complete plan)

## Files Created/Modified
- `frontend/src/components/session/ScheduleTimeline.tsx` - Changed line 140 to pass only day-of entries to CookingGantt via `.filter((e) => !e.is_prep_ahead)`
- `frontend/src/components/session/CookingGantt.tsx` - Added defensive dayOfTimeline filter, removed hasPrepAhead memo and legend block, added tabIndex={0}
- `frontend/src/components/session/CookingGantt.module.css` - height: 40px on barArea, min-width: 8px on barGroup and bar, added :focus-visible rule

## Decisions Made
- Filter at source (ScheduleTimeline) is the root-cause fix; defensive filter in CookingGantt guards against regressions
- Removed prep-ahead legend from Gantt since it will never show with the new filter — the legend belongs in the prep-ahead section that already exists in ScheduleTimeline
- Bar positioning math was already correct (percentage-based); data pollution was the only bug

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None - TypeScript compiled cleanly, all acceptance criteria met.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- CookingGantt data pipeline is now clean and correct
- Task 2 (human-verify checkpoint) is pending: user should verify bars render for all day-of steps
- Once verified, Phase 1 Plan 2 can proceed with clock-time axis work

---
*Phase: 01-gantt-fix*
*Completed: 2026-03-19*
