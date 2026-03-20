# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — Schedule UI & Pipeline Fixes

**Shipped:** 2026-03-20
**Phases:** 3 | **Plans:** 4

### What Was Built
- Gantt chart with correct bar sizing, positioning, and buffer zone rendering
- Absolute clock-time x-axis with dynamic 15/30/60-min intervals and horizontal scroll
- Tightened prep-ahead classification with enricher allow/deny list and renderer time-gate
- Unified timeline merging prep-ahead and day-of into single view with inline tags

### What Worked
- Phase execution was fast (4 plans in ~60 min total) — well-scoped plans with clear must-haves
- User-driven iteration caught real issues: empty Gantt space from prep-ahead offsets, bars merging into single blocks, lack of visual separation between bars
- Backwards compatibility approach (merging old `prep_ahead_entries` at render time) avoids data migration

### What Was Inefficient
- Phase 2 (prep-ahead fix) was partially superseded by Phase 3 (unified timeline) — the split-based approach was designed and implemented, then abandoned for unification
- Multiple screenshot-debug cycles needed for the bar merging issue — `MERGE_GAP_MINUTES` default of 5 was wrong for the unified timeline context

### Patterns Established
- `_is_meaningful_prep_ahead()` time-gate pattern: boolean + string window → composite classification
- Clock-time helpers that normalize ISO, 12-hour, and 24-hour formats to consistent display
- Day-of window rebasing to eliminate empty chart space from offset-based positioning
- Individual bars with visual separation (border-right + box-shadow) for step-level clarity

### Key Lessons
1. LLM classification (enricher prompt) needs both allow-list AND deny-list — vague instructions lead to over-classification
2. UI decisions should be validated with screenshots early — bar merging and visual separation issues were only caught visually
3. When data flow changes (prep-ahead unification), downstream components may need parameter adjustments (MERGE_GAP_MINUTES)

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Phases | Plans | Key Change |
|-----------|--------|-------|------------|
| v1.0 | 3 | 4 | Initial milestone — established phase/plan workflow |

### Cumulative Quality

| Milestone | Tests | Key Metric |
|-----------|-------|------------|
| v1.0 | 138 | All passing, 0 failures |

### Top Lessons (Verified Across Milestones)

1. Visual UI work requires screenshot validation — automated tests catch data issues but not layout/UX problems
2. LLM prompt engineering needs explicit constraints (allow/deny lists) not vague guidance
