# Requirements: GRASP — Gantt Chart Fix

**Defined:** 2026-03-18
**Core Value:** The Gantt chart must accurately show every cooking step as a correctly-sized bar on an absolute clock-time axis

## v1 Requirements

### Gantt Bars

- [ ] **GANTT-01**: Every `TimelineEntry` in the schedule renders as a visible bar in its recipe lane
- [ ] **GANTT-02**: Bar width accurately reflects step `duration_minutes` proportional to total timeline span
- [ ] **GANTT-03**: Bar horizontal position accurately reflects step `time_offset_minutes`
- [ ] **GANTT-04**: Bars with `buffer_minutes` (duration uncertainty) show buffer zone visually

### Time Axis

- [ ] **TIME-01**: X-axis displays absolute clock times (e.g., "4:00 PM", "4:30 PM") using `clock_time` from `TimelineEntry`
- [ ] **TIME-02**: Time markers are spaced at sensible intervals based on total duration

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
| GANTT-01 | — | Pending |
| GANTT-02 | — | Pending |
| GANTT-03 | — | Pending |
| GANTT-04 | — | Pending |
| TIME-01 | — | Pending |
| TIME-02 | — | Pending |
| TABLE-01 | — | Pending |
| TABLE-02 | — | Pending |

**Coverage:**
- v1 requirements: 8 total
- Mapped to phases: 0
- Unmapped: 8 ⚠️

---
*Requirements defined: 2026-03-18*
*Last updated: 2026-03-18 after initial definition*
