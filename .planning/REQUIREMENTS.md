# Requirements: GRASP — Gantt Chart Fix

**Defined:** 2026-03-18
**Core Value:** The Gantt chart must accurately show every cooking step as a correctly-sized bar on an absolute clock-time axis

## v1 Requirements

### Gantt Bars

- [x] **GANTT-01**: Every `TimelineEntry` in the schedule renders as a visible bar in its recipe lane
- [x] **GANTT-02**: Bar width accurately reflects step `duration_minutes` proportional to total timeline span
- [x] **GANTT-03**: Bar horizontal position accurately reflects step `time_offset_minutes`
- [x] **GANTT-04**: Bars with `buffer_minutes` (duration uncertainty) show buffer zone visually

### Time Axis

- [x] **TIME-01**: X-axis displays absolute clock times (e.g., "4:00 PM", "4:30 PM") using `clock_time` from `TimelineEntry`
- [x] **TIME-02**: Time markers are spaced at sensible intervals based on total duration

### Prep-Ahead Classification

- [ ] **PREP-01**: Enricher prompt restricts `can_be_done_ahead=true` to steps requiring extended lead time — brining, marinating (4+ hours), stock-making, dough proofing, curing, setting gelatin, fermenting — not quick tasks like herb rubs, chopping, or mixing
- [ ] **PREP-02**: Renderer applies a time-gate: only steps with `prep_ahead_window` containing "hours" or "days" are marked `is_prep_ahead`; steps with short windows stay day-of
- [ ] **PREP-03**: Test fixtures reflect tightened criteria with correct prep-ahead expectations

## v2 Requirements

### Enhancements

- **ENH-01**: Interactive hover on bars shows full step details
- **ENH-02**: Click-to-scroll between Gantt bar and corresponding Day of Timeline entry
- **ENH-03**: Responsive layout for smaller screens

## Out of Scope

| Feature | Reason |
|---------|--------|
| Drag-to-reschedule | Not a scheduling editor, just a visualization |
| Day of Timeline section rewrite | Separate component, separate scope |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| GANTT-01 | Phase 1 | Complete |
| GANTT-02 | Phase 1 | Complete |
| GANTT-03 | Phase 1 | Complete |
| GANTT-04 | Phase 1 | Complete |
| TIME-01 | Phase 1 | Complete |
| TIME-02 | Phase 1 | Complete |
| PREP-01 | Phase 2 | Pending |
| PREP-02 | Phase 2 | Pending |
| PREP-03 | Phase 2 | Pending |

**Coverage:**
- v1 requirements: 9 total
- Mapped to phases: 9
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-18*
*Last updated: 2026-03-18 after roadmap creation*
