---
status: complete
phase: 01-gantt-fix
source: 01-01-SUMMARY.md
started: 2026-03-19T04:00:00Z
updated: 2026-03-19T04:10:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Gantt bars render for all day-of steps
expected: Open a completed session. The "Steps at a Glance" Gantt chart shows one lane per recipe. Each lane has colored bars representing cooking activity blocks. No bars are missing, and no prep-ahead entries appear in the chart.
result: pass

### 2. Gantt bars show step numbers
expected: Each bar in the Gantt chart displays a step number (e.g., "1", "2") or a range for merged bars (e.g., "1–3"). Hovering a bar shows a tooltip with numbered step details.
result: pass

### 3. Gantt chart fits without scrolling
expected: The entire Gantt chart fits within the container width — no horizontal scrollbar is visible.
result: pass

### 4. Day-of Recipe Steps section
expected: Below Prep Ahead, a section titled "Day-of Recipe Steps" lists all cooking steps. Each step shows a colored number prefix (matching the recipe's Gantt color), recipe name, action text, and duration.
result: pass

### 5. Colored left border on Day-of steps
expected: Each step in "Day-of Recipe Steps" has a colored left border matching the recipe's lane color in the Gantt chart. The borders align vertically with the Prep Ahead section's left border.
result: pass

### 6. Resource legend removed
expected: There is no "RESOURCES: Hands-on / Stovetop / Oven / Passive" legend anywhere on the schedule page. Resource badges (STOVETOP, HANDS, etc.) are not shown on individual steps.
result: pass

### 7. Heads-up callouts are subtle
expected: Steps with timing notes (e.g., "20–25 min depending on stovetop heat") show them as soft italic text, not bold orange warning boxes.
result: pass

## Summary

total: 7
passed: 7
issues: 0
pending: 0
skipped: 0

## Gaps

[none yet]
