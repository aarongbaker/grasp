# GRASP — Schedule Visualization

## What This Is

The schedule visualization layer of GRASP, a web-based dinner party planning tool. The Gantt chart and timeline components show every cooking step as correctly-sized bars on an absolute clock-time axis, with a unified chronological view that reveals gaps and parallel tasks across recipes.

## Core Value

The cook can see at a glance what to do and when — every step visible, accurately timed, in one unified view.

## Requirements

### Validated

- ✓ Every TimelineEntry renders as a visible bar in its recipe lane — v1.0
- ✓ Bar widths reflect step durations proportionally — v1.0
- ✓ Bar positions reflect step start times accurately — v1.0
- ✓ Buffer uncertainty zones shown visually on bars — v1.0
- ✓ X-axis displays absolute clock times at sensible intervals — v1.0
- ✓ Prep-ahead restricted to long-lead tasks (brining, marinating, stock-making) — v1.0
- ✓ Renderer time-gate filters by hours/days window — v1.0
- ✓ All steps in single chronological timeline — no separate prep-ahead section — v1.0
- ✓ Inline prep-ahead tags for steps that can be done ahead — v1.0
- ✓ Gantt renders all steps with gaps and parallel tasks visible — v1.0
- ✓ Backend returns unified timeline list — v1.0

### Active

(None — next milestone TBD)

### Out of Scope

- Drag-to-reschedule — visualization only, not a scheduling editor
- Day-of Timeline section rewrite — separate component, separate scope
- Interactive hover with full step details — v2 enhancement
- Click-to-scroll between Gantt bar and timeline entry — v2 enhancement
- Responsive layout for smaller screens — v2 enhancement

## Context

Shipped v1.0 with 1,667 net lines across 39 files (Python backend + React frontend).
Tech stack: React with CSS Modules, FastAPI, LangGraph pipeline.
Full pipeline: generator → enricher → validator → dag_builder → dag_merger → renderer.
138 tests passing (unit + fixture-based).

## Constraints

- **Tech stack**: React with CSS Modules, no new dependencies
- **Data source**: Works with existing `TimelineEntry` type — no backend schema changes needed
- **Design**: Follows CLAUDE.md warm editorial aesthetic (dark theme, copper accents, serif headings)
- **Backwards compat**: Old session data with separate `prep_ahead_entries` handled via frontend merge

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Use `clock_time` field for x-axis | Already provided by backend, gives absolute times | ✓ Good |
| Keep lane-per-recipe layout | Steps as individual bars grouped by recipe | ✓ Good |
| Filter prep-ahead at ScheduleTimeline handoff | Root-cause fix vs filtering in CookingGantt | ✓ Good (superseded by unification) |
| Dynamic interval logic (15/30/60 min) | Match natural session lengths for private chefs | ✓ Good |
| Rebase axis to day-of window | Eliminate empty space from prep-ahead offsets | ✓ Good |
| Time-gate with string contains ("hour"/"day"/"week") | Simpler than regex, handles natural language | ✓ Good |
| Explicit enricher allow/deny list | Prevents LLM from over-classifying prep-ahead | ✓ Good |
| Unified timeline (Phase 3) | Prep-ahead steps depend on day-of steps; separate section misleading | ✓ Good |
| MERGE_GAP_MINUTES = -1 | Each step gets own bar for clear timing visibility | ✓ Good |
| Legacy merge at render time | Old sessions with prep_ahead_entries still work | ✓ Good |

---
*Last updated: 2026-03-20 after v1.0 milestone*
