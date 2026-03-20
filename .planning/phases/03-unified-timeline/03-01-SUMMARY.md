---
phase: 03-unified-timeline
plan: 01
subsystem: timeline-rendering
tags: [renderer, frontend, timeline, gantt, pdf, testing]
dependency_graph:
  requires: [02-01]
  provides: [unified-timeline]
  affects: [ScheduleTimeline, CookingGantt, RecipePDF, renderer]
tech_stack:
  added: []
  patterns:
    - Unified list pattern: single timeline[] instead of (day_of, prep_ahead) tuple
    - Backwards compatibility via legacy prep_ahead_entries merge at render time
    - Inline tagging: prep_ahead_window shown as badge in TimelineRow
key_files:
  created: []
  modified:
    - graph/nodes/renderer.py
    - frontend/src/components/session/ScheduleTimeline.tsx
    - frontend/src/components/session/ScheduleTimeline.module.css
    - frontend/src/components/session/CookingGantt.tsx
    - frontend/src/components/session/RecipePDF.tsx
    - tests/fixtures/schedules.py
    - tests/test_phase7_unit.py
decisions:
  - "_build_timeline() returns single list — no tuple split at construction time"
  - "prep_ahead_entries=[] on NaturalLanguageSchedule for backwards compatibility"
  - "is_prep_ahead flag preserved on entries for data integrity and inline tagging"
  - "Legacy session data (old prep_ahead_entries) merged+sorted at render time in both ScheduleTimeline and RecipePDF"
  - "CookingGantt receives full timeline — all steps visible in Gantt including prep-ahead"
metrics:
  duration_minutes: 5
  completed_date: "2026-03-20"
  tasks_completed: 3
  tasks_total: 3
  files_modified: 7
---

# Phase 03 Plan 01: Unify Timeline Summary

**One-liner:** Merged separate "Prep Ahead" and "Day-of Steps" sections into a single chronological timeline — prep-ahead steps show inline badge tags and appear in the Gantt chart.

## What Was Built

The timeline rendering pipeline now outputs a single unified list of `TimelineEntry` objects rather than splitting prep-ahead steps into a separate collection. All steps are visible in the Gantt chart, making gaps and parallel tasks fully visible.

### Backend (renderer.py)

- `_build_timeline()` signature changed from `-> tuple[list, list]` to `-> list[TimelineEntry]`
- All entries returned in chronological order; `is_prep_ahead` flag preserved for data integrity
- `schedule_renderer_node()` sets `prep_ahead_entries=[]` on `NaturalLanguageSchedule` (backwards compat field kept, always empty now)
- `_format_schedule_for_prompt()` uses `[can do ahead: {window}]` suffix instead of `[prep-ahead: ...]`

### Frontend (ScheduleTimeline.tsx)

- Removed `PrepItem` component entirely
- Removed separate "Prep Ahead" section
- `TimelineRow` now shows an inline `prepAheadTag` badge when `entry.prep_ahead_window` is set
- Section renamed from "Day-of Recipe Steps" to "Recipe Steps"
- Full `allEntries` list passed to `CookingGantt` (no `is_prep_ahead` filter)
- Backwards compat: if `schedule.prep_ahead_entries` has entries (old session data), they are merged+sorted into the unified list

### Frontend (CookingGantt.tsx)

- Removed `dayOfTimeline` useMemo that filtered `!e.is_prep_ahead`
- All references to `dayOfTimeline` replaced with `timeline` directly
- All steps now render as Gantt bars — prep-ahead steps appear in their own recipe lane

### Frontend (RecipePDF.tsx)

- Two `TimelineSection` calls (`Prep Ahead` + `Day-Of Timeline`) merged into one `Timeline` section
- Same backwards-compat merge logic as ScheduleTimeline

### CSS (ScheduleTimeline.module.css)

- Added `.prepAheadTag` style: `accent-cool` text, subtle transparent background, thin border — calm indicator matching the kitchen instrument aesthetic

### Tests

- `_split_timeline()` fixture helper replaced by `_build_unified_timeline()` — returns single list
- `NATURAL_LANGUAGE_SCHEDULE_FULL/TWO_RECIPE` fixtures updated: `prep_ahead_entries=[]`, timeline includes all 12/7 entries
- `TestBuildTimeline` assertions updated for unified return value
- `TestScheduleRendererNode` assertions updated for unified counts
- 138 tests pass (0 failures)

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

### Files exist
- `/Users/aaronbaker/Desktop/Projects/grasp/graph/nodes/renderer.py` — modified
- `/Users/aaronbaker/Desktop/Projects/grasp/frontend/src/components/session/ScheduleTimeline.tsx` — modified
- `/Users/aaronbaker/Desktop/Projects/grasp/frontend/src/components/session/CookingGantt.tsx` — modified
- `/Users/aaronbaker/Desktop/Projects/grasp/frontend/src/components/session/RecipePDF.tsx` — modified
- `/Users/aaronbaker/Desktop/Projects/grasp/tests/fixtures/schedules.py` — modified
- `/Users/aaronbaker/Desktop/Projects/grasp/tests/test_phase7_unit.py` — modified

### Commits exist
- `02b2fbc` — fix(03-01): unify renderer timeline — stop splitting prep-ahead
- `807dfab` — feat(03-01): unify frontend timeline — all steps in one view with inline tags
- `69ef769` — test(03-01): update fixtures for unified timeline

### Test run: PASSED (138 passed, 0 failed)

## Self-Check: PASSED
