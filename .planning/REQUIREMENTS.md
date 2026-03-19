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

### Step Table

- [ ] **TABLE-01**: Table alongside chart shows step name, start time, end time, and duration for each step
- [ ] **TABLE-02**: Table rows are grouped or color-coded by recipe to match Gantt lane colors

## v2 Requirements

### Enhancements

- **ENH-01**: Interactive hover on bars shows full step details
- **ENH-02**: Click-to-scroll between Gantt bar and corresponding Day of Timeline entry
- **ENH-03**: Responsive layout for smaller screens

## Out of Scope

| Feature | Reason |
|---------|--------|
| Backend pipeline changes | Schedule data is correct; only frontend rendering is broken |
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
| TABLE-01 | Phase 2 | Pending |
| TABLE-02 | Phase 2 | Pending |

**Coverage:**
- v1 requirements: 8 total
- Mapped to phases: 8
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-18*
*Last updated: 2026-03-18 after roadmap creation*
