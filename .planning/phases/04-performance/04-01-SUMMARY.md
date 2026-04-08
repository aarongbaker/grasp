---
phase: 04-performance
plan: 01
subsystem: scheduler
tags: [scheduler, profiling, dag_merger, performance, pytest]
requires:
  - phase: 03
    provides: a security-hardened scheduler surface and a green backend baseline so Phase 4 could isolate performance evidence without overlapping correctness or auth work
provides:
  - A repeatable phase-local `_merge_dags()` benchmark harness
  - Recorded scheduler profile evidence showing `_IntervalIndex` already keeps slot-finding cost acceptable at the Phase 4 workload
  - Green scheduler regression confirmation with no production `dag_merger.py` change
affects: [phase-04, scheduler, dag-merger, profiling, milestone-close]
tech-stack:
  added: []
  patterns:
    - Keep performance evidence as a phase-local benchmark artifact when machine-specific timing asserts would be too flaky for pytest
    - Close correctness-sensitive performance phases as docs-only when the measured hotspot no longer justifies code churn
key-files:
  created:
    - .planning/phases/04-performance/benchmark_dag_merger.py
    - .planning/phases/04-performance/04-PROFILE.md
  modified:
    - .planning/ROADMAP.md
    - .planning/STATE.md
key-decisions:
  - "Left `app/graph/nodes/dag_merger.py` unchanged because the measured `12` recipe / `60` step workload stayed at `1.314 ms` median and did not show the original overlap-scan concern."
  - "Stored the benchmark harness inside the Phase 4 directory so later audits can rerun the exact command used to justify the docs-only close."
patterns-established:
  - "Profile `_merge_dags()` end-to-end instead of timing `_IntervalIndex` in isolation when evaluating scheduler concerns."
  - "Prefer preserving a green scheduler contract over speculative micro-optimizations once benchmark evidence is already comfortably inside target bounds."
requirements-completed: [PERF-03]
duration: 2min
completed: 2026-04-08
---

# Phase 04 Plan 01: Scheduler Profiling Proof Summary

**A repeatable `_merge_dags()` benchmark now proves the scheduler is already fast enough at the Phase 4 workload, so the milestone closes this phase without changing production scheduling logic.**

## Performance

- **Duration:** 2 min
- **Started:** 2026-04-08T22:21:35Z
- **Completed:** 2026-04-08T22:23:19Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Added a phase-local benchmark helper that exercises `_merge_dags()` on realistic 20/40/60/80 step menus and prints both scaling data and cProfile hotspots.
- Recorded the benchmark output in `04-PROFILE.md`, including the exact rerunnable command and the explicit `docs-only proof` decision.
- Re-ran the locked scheduler regression gate with `tests/test_kitchen_edge_cases.py` and `tests/test_phase6_unit.py` green.

## Task Commits

Each task will be represented by the same docs-only closure commit for this plan.

1. **Task 1: Add a repeatable scheduler benchmark and capture the Phase 4 profile** - `ab8746e` (`docs`)
2. **Task 2: Close the phase with either a docs-only proof or a narrow hot-path fix** - `ab8746e` (`docs`, docs-only proof path)

**Plan metadata:** `c3aaf4f` (`docs`)

## Files Created/Modified
- `.planning/phases/04-performance/benchmark_dag_merger.py` - rerunnable benchmark harness for realistic `_merge_dags()` workloads
- `.planning/phases/04-performance/04-PROFILE.md` - captured scaling table, cProfile hotspots, and the final performance decision
- `.planning/phases/04-performance/04-01-SUMMARY.md` - phase completion record for the docs-only proof path

## Decisions Made
- Chose the docs-only close because the measured `12` recipe / `60` step workload stayed around `1.314 ms` median and the profile did not reveal the feared overlap-scan blow-up.
- Kept the benchmark outside pytest timing assertions to avoid flaky machine-dependent thresholds while still making the evidence reproducible.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The first benchmark-script run failed with `ModuleNotFoundError: No module named 'app'` because executing a script by path does not automatically include the repo root on `sys.path`. The helper was patched to prepend the repo root before the final profiling run.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All four hardening phases are now complete.
- The next workflow step is milestone wrap-up rather than more phase work.

## Self-Check: PASSED

---
*Phase: 04-performance*
*Completed: 2026-04-08*
