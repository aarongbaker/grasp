# GRASP — Gantt Chart Fix

## What This Is

A focused fix for the Gantt chart on the sessions page in GRASP, a web-based dinner party planning tool. The chart visualizes cooking step timing across recipes but currently has broken sizing/proportions, missing bars, and uses relative time offsets instead of absolute clock times.

## Core Value

The Gantt chart must accurately show every cooking step as a correctly-sized bar on an absolute clock-time axis, so the cook can see at a glance what to do and when.

## Requirements

### Validated

- ✓ LangGraph pipeline generates `NaturalLanguageSchedule` with `TimelineEntry` objects — existing
- ✓ `TimelineEntry` includes `clock_time`, `time_offset_minutes`, `duration_minutes`, `recipe_name`, `step_id`, `action`, `resource` — existing
- ✓ `CookingGantt` component renders lane-based chart with recipe color coding — existing
- ✓ Frontend fetches session results via `getSessionResults()` — existing

### Active

- [ ] Fix bar sizing/proportions — bars should accurately reflect step duration
- [ ] Fix missing bars — all timeline steps must render as visible bars
- [ ] Switch x-axis from relative offsets (+30m) to absolute clock times (e.g., 5:00 PM)
- [ ] Table alongside chart shows: step name, start time, end time, duration

### Out of Scope

- Backend pipeline changes — the schedule data is correct, only the frontend rendering is broken
- Recipe bars or nested bars — each bar represents an individual step, grouped by recipe in lanes
- Redesigning the overall session detail page — only the Gantt chart section

## Context

- GRASP is a React + FastAPI app with a LangGraph-based meal planning pipeline
- The `CookingGantt` component lives at `frontend/src/components/session/CookingGantt.tsx`
- Parent component is `ScheduleTimeline.tsx` which wraps the Gantt plus other schedule views
- `TimelineEntry` already has a `clock_time` field (string | null) that's not being used by the Gantt
- The current chart uses `time_offset_minutes` and `totalDurationMinutes` for positioning — this may be the source of sizing bugs
- Design system follows warm editorial aesthetic per CLAUDE.md (dark theme, copper accents, serif headings)

## Constraints

- **Tech stack**: React with CSS Modules, no new dependencies needed
- **Data source**: Must work with existing `TimelineEntry` type from the API — no backend changes
- **Design**: Must follow CLAUDE.md aesthetic guidelines (warm dark theme, copper accents)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Use `clock_time` field for x-axis | Already provided by backend, gives absolute times | — Pending |
| Keep lane-per-recipe layout | User confirmed steps should be individual bars grouped by recipe | — Pending |

---
*Last updated: 2026-03-18 after initialization*
