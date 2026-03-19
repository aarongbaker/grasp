# Roadmap: GRASP — Gantt Chart Fix

## Overview

Two-phase fix for the `CookingGantt` component. Phase 1 corrects the chart itself — bars must render with accurate sizing, positioning, and an absolute clock-time x-axis. Phase 2 adds the companion step table that surfaces the same data in tabular form alongside the chart.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Gantt Fix** - Fix bar sizing, positioning, missing bars, and switch x-axis to absolute clock times
- [ ] **Phase 2: Step Table** - Add companion table showing step name, start time, end time, and duration per step

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
**Plans:** 2 plans
Plans:
- [ ] 01-01-PLAN.md — Fix data pipeline and bar rendering (GANTT-01 through GANTT-04)
- [ ] 01-02-PLAN.md — Add clock-time x-axis with dynamic intervals and horizontal scroll (TIME-01, TIME-02)

### Phase 2: Step Table
**Goal**: A companion step table alongside the chart gives the cook a scannable, structured view of the same schedule data
**Depends on**: Phase 1
**Requirements**: TABLE-01, TABLE-02
**Success Criteria** (what must be TRUE):
  1. A table appears alongside the Gantt chart listing each step with its name, start time, end time, and duration
  2. Table rows are grouped or color-coded by recipe, matching the lane colors in the chart above
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Gantt Fix | 0/2 | Not started | - |
| 2. Step Table | 0/TBD | Not started | - |
