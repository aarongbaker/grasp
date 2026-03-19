# Phase 1: Gantt Fix - Context

**Gathered:** 2026-03-18
**Status:** Ready for planning

<domain>
## Phase Boundary

Fix the CookingGantt component so every day-of cooking step renders as a correctly-sized, correctly-positioned bar on an absolute clock-time x-axis. No backend changes — only frontend rendering fixes in `CookingGantt.tsx` and its CSS module.

</domain>

<decisions>
## Implementation Decisions

### Prep-ahead bar handling
- Only day-of steps appear on the Gantt chart — exclude `is_prep_ahead` entries
- Prep-ahead already has its own dedicated section below the chart in ScheduleTimeline
- Root cause of missing bars: currently `ScheduleTimeline.tsx:140` passes `[...schedule.timeline, ...(schedule.prep_ahead_entries ?? [])]` to the Gantt, mixing prep-ahead entries with day-of entries. Prep-ahead entries likely have offset values that break chart positioning.

### Lane layout
- Keep separate lanes per recipe — bars never overlap across recipes
- Each recipe gets its own horizontal row

### Bar labels & hover
- Keep current one-keyword label extracted from action text (e.g., "Preheat", "Bake")
- Keep current browser-native title attribute for hover — no custom tooltip needed

### Clock time formatting
- Use 12-hour format: "4:00 PM", "4:30 PM", "5:00 PM"
- Parse `clock_time` field from `TimelineEntry` (already provided by backend)
- Fallback: if `clock_time` is null (no serving time set), fall back to relative offsets (+0m, +30m, +1h) like current behavior

### Chart proportions
- Fixed chart width with horizontal scroll for long sessions — don't scale-to-fit
- Lane height increased from 32px to 40px for better readability
- Time markers at sensible intervals (e.g., every 15 or 30 minutes depending on total duration)

### Claude's Discretion
- Exact minimum bar width for very short steps
- Time marker interval logic (15 vs 30 min based on total duration)
- Horizontal scroll container implementation
- Bar color palette (keep existing warm palette from CLAUDE.md)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Gantt chart component
- `frontend/src/components/session/CookingGantt.tsx` — Current Gantt implementation (the file being fixed)
- `frontend/src/components/session/CookingGantt.module.css` — Current Gantt styles
- `frontend/src/components/session/ScheduleTimeline.tsx` — Parent component that passes data to CookingGantt

### Data types
- `frontend/src/types/api.ts` — `TimelineEntry`, `NaturalLanguageSchedule` type definitions

### Design system
- `CLAUDE.md` — UI design guidelines, color palette, typography, spatial composition rules

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `CookingGantt.tsx`: Lane-based layout with recipe color mapping already works — fix data filtering and sizing, don't rewrite
- `CookingGantt.module.css`: Existing bar, lane, grid, and legend styles are well-structured
- `LANE_COLORS` array: Warm palette already matching CLAUDE.md aesthetic
- `oneWord()` helper: Extracts bar label from action text — keep as-is

### Established Patterns
- CSS Modules for component styling (`.module.css` files)
- `useMemo` for derived data (lanes, markers, color mapping)
- Percentage-based positioning within bar area (left/width as %)
- `TimelineEntry.clock_time` field exists but is currently unused by the Gantt

### Integration Points
- `ScheduleTimeline.tsx:140` — Where the Gantt receives its data. Must change to filter out prep-ahead entries
- `CookingGantt` props: `timeline` and `totalDurationMinutes` — may need to adjust prop interface for clock time support

</code_context>

<specifics>
## Specific Ideas

- User shared screenshot showing only 3-4 bars rendering (Preheat, Bake, Preh...) out of many more steps
- Bars that do render have incorrect proportions relative to their actual duration
- The "Day of Timeline" section below shows many more steps than the Gantt chart displays, confirming data exists but isn't rendering

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-gantt-fix*
*Context gathered: 2026-03-18*
