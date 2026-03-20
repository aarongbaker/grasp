# Roadmap: GRASP — Schedule UI & Pipeline Fixes

## Overview

Fix the schedule visualization and data pipeline. Phase 1 fixed the Gantt chart rendering. Phase 2 tightens prep-ahead classification so only long-lead tasks (brining, marinating, stock-making) are separated from the day-of timeline.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Gantt Fix** - Fix bar sizing, positioning, missing bars, and switch x-axis to absolute clock times
- [x] **Phase 2: Prep-Ahead Fix** - Tighten prep-ahead classification so only long-lead tasks are separated from day-of (completed 2026-03-20)

## Phase Details

### Phase 1: Gantt Fix
**Goal**: The Gantt chart accurately renders every cooking step as a correctly-sized bar on an absolute clock-time axis
**Depends on**: Nothing (first phase)
**Requirements**: GANTT-01, GANTT-02, GANTT-03, GANTT-04, TIME-01, TIME-02
**Success Criteria** (what must be TRUE):
  1. Every step in the schedule appears as a visible bar in its recipe lane — no missing bars
  2. Bar widths reflect actual step durations proportionally (a 30-minute step is twice as wide as a 15-minute step)
  3. Bar horizontal positions reflect step start times accurately relative to each other
  4. Steps with buffer uncertainty show a visually distinct buffer zone on their bar
  5. The x-axis shows absolute clock times (e.g., "4:00 PM", "4:30 PM") at sensible intervals, not relative offsets
**Plans:** 2/2 plans complete
**Status:** Complete
**Completed:** 2026-03-19
Plans:
- [x] 01-01-PLAN.md — Fix data pipeline and bar rendering (GANTT-01 through GANTT-04)
- [x] 01-02-PLAN.md — Add clock-time x-axis with dynamic intervals and horizontal scroll (TIME-01, TIME-02)
**Notes:** Phase evolved significantly during execution. Beyond the original plans, the Gantt was redesigned with merged activity bars, step numbering, no-scroll fit, and the Day-of Recipe Steps section was overhauled with recipe-colored borders and step numbers.

### Phase 2: Prep-Ahead Fix
**Goal**: Only long-lead tasks (brining, marinating, stock-making, dough proofing, curing) are classified as prep-ahead; quick prep tasks stay in the day-of timeline
**Depends on**: Phase 1
**Requirements**: PREP-01, PREP-02, PREP-03
**Success Criteria** (what must be TRUE):
  1. The enricher prompt restricts `can_be_done_ahead=true` to steps requiring extended lead time (hours/days), not quick prep tasks
  2. The renderer applies a time-gate so only steps with meaningful prep-ahead windows (hours/days) are marked `is_prep_ahead`
  3. Test fixtures and expectations reflect the tightened criteria
**Plans:** 1/1 plans complete
**Status:** Not started

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Gantt Fix | 2/2 | Complete   | 2026-03-19 |
| 2. Prep-Ahead Fix | 1/1 | Complete   | 2026-03-20 |
