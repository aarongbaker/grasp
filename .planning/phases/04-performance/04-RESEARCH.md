# Phase 4: Performance - Research

**Researched:** 2026-04-08
**Status:** Ready for planning

## Scope Anchors

- `PERF-03`: profile the scheduler under a realistic `10+` recipe / `50+` step menu and only change production code if the current slot-finding path still shows problematic growth.

## Current Code Surface

### Scheduler path already using interval indexes

- `app/graph/nodes/dag_merger.py::_find_earliest_start()` already uses `_IntervalIndex.count_overlapping()` and `_IntervalIndex.min_end_after()` for pooled resources, so the original concern about repeated linear overlap scans is no longer accurate for the shared-resource path.
- `app/graph/nodes/dag_merger.py::_find_stovetop_slot()` already uses a per-burner `_IntervalIndex` map and advances to the next burner release boundary instead of rescanning a shared linear interval list.
- `equipment_intervals` in `_merge_dags()` also use `_IntervalIndex`, so the remaining scheduler work is dominated by bounded burner-slot evaluation plus general DAG bookkeeping rather than one obvious legacy overlap scan.

### Remaining likely cost centers

- `_merge_dags()` still sorts the ready queue on every scheduling iteration.
- `_build_planned_oven_intervals()` and `_compute_critical_paths()` each run NetworkX topological passes for every merge.
- Pydantic model construction and attribute writes still contribute noticeable constant cost inside the merger.
- `_find_stovetop_slot()` still scans every burner slot at each candidate release boundary, but `max_burners` is hard-capped at `10`, so that scan is bounded and must be profiled before being treated as a bottleneck.

### Benchmark harness gap

- The repo currently has scheduler correctness tests but no committed benchmark or profiling helper for `_merge_dags()`.
- Phase 4 therefore needs a repeatable profiling artifact, not just an ad hoc terminal note, so later audits can rerun the same workload.

## Constraints That Matter For Planning

- The benchmark must exercise the real `_merge_dags()` path, not `_IntervalIndex` in isolation.
- The workload must hit the roadmap threshold: roughly `10+` recipes and `50+` steps.
- `tests/test_kitchen_edge_cases.py` and `tests/test_phase6_unit.py` remain the regression gate whether Phase 4 closes as docs-only or with a narrow fix.
- If profiling shows the current implementation is already adequate, the correct output is a documented proof phase with no production scheduler change.
- If profiling still shows a hotspot, the fix must stay inside `app/graph/nodes/dag_merger.py` and target the measured seam only.

## Recommended Phase Split

### Wave 1 - profile, decide, and close

- Plan `04-01`: add a repeatable phase-local benchmark harness, capture scaling and cProfile evidence for a `10+` recipe / `50+` step workload, then either:
  - record a docs-only completion if `_IntervalIndex` already keeps slot-finding cost acceptable, or
  - land the smallest safe hot-path optimization justified by the profile and rerun the scheduler regression suite.

One plan is sufficient because the phase has a single requirement, a single production seam, and an explicit docs-only exit path.

## Critical Pitfalls

1. **Optimizing before measuring**
   - The locked project decision is to profile first. Any production refactor without evidence would violate the phase boundary.

2. **Benchmarking the wrong seam**
   - Profiling `_IntervalIndex` methods alone would miss queue sorting, burner-slot selection, and topological work inside `_merge_dags()`.

3. **Turning performance proof into a flaky test**
   - The benchmark should be a repeatable artifact or helper command, not a pytest assertion with machine-specific timing thresholds.

4. **Expanding scope into scheduler architecture**
   - If a fix is needed, it should stay narrow. Phase 4 is not a DAG-merger rewrite.

## Validation Architecture

### Quick checks

- `./.venv/bin/python .planning/phases/04-performance/benchmark_dag_merger.py`

### Regression gate

- `./.venv/bin/python -m pytest -q tests/test_kitchen_edge_cases.py tests/test_phase6_unit.py -o addopts=''`

### Evidence expectations

- Record the exact benchmark command, the scaling table, and the top cumulative cProfile frames in a phase artifact.
- Record the final decision explicitly: either `docs-only close` or `narrow hot-path fix required`.

## Planning Implications

- The plan should create the benchmark helper inside the phase directory so the evidence stays co-located with the phase artifacts.
- The execute plan should treat `app/graph/nodes/dag_merger.py` as conditional scope: read it first, but only modify it if the benchmark shows it is still the bottleneck.
- The phase summary must explain why production code was or was not changed, backed by the profiling artifact and the regression gate.
