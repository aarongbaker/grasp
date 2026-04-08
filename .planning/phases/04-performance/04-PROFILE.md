# Phase 4 Scheduler Profile

**Captured:** 2026-04-08
**Workload:** realistic `_merge_dags()` benchmark with 4/8/12/16 recipe menus and 5 steps per recipe
**Primary decision:** docs-only proof

## Command

```bash
./.venv/bin/python .planning/phases/04-performance/benchmark_dag_merger.py
```

## Scaling Results

| Recipes | Steps | Median (ms) | P95 (ms) | Total Duration (min) |
|---------|-------|-------------|----------|----------------------|
| 4 | 20 | 0.334 | 0.602 | 119 |
| 8 | 40 | 0.763 | 0.830 | 199 |
| 12 | 60 | 1.314 | 1.630 | 279 |
| 16 | 80 | 2.006 | 2.351 | 359 |

### 12 recipes / 60 steps

- Median runtime stays at `1.314 ms`.
- P95 stays at `1.630 ms`.
- Doubling from `20` to `40` steps and then growing to `60` and `80` steps produces a smooth, low-latency curve rather than a blow-up near the roadmap threshold.

## Top cProfile Frames

Profile sample: `1000` executions of the `12` recipe / `60` step workload.

| Frame | Cumulative Time (s) | Interpretation |
|-------|---------------------|----------------|
| `_merge_dags()` | 3.316 | total scheduler cost across 1000 runs |
| `_find_stovetop_slot()` | 0.395 | largest single scheduling helper, but still only ~`0.395 ms/run` |
| `pydantic.main.__setattr__` | 0.344 | model mutation overhead |
| `_build_planned_oven_intervals()` | 0.262 | planned oven-window bookkeeping |
| `_compute_critical_paths()` | 0.217 | graph-analysis cost |
| `list.sort` | 0.161 | ready-queue / local ordering overhead |
| `_find_earliest_start()` | 0.156 | pooled-resource search cost |

Notably absent: any dominant legacy linear overlap scan. The current `_IntervalIndex` path is already containing overlap checks well enough that constant-cost Python work and graph bookkeeping are now comparable or larger contributors.

## Decision

Decision: `_IntervalIndex` already keeps the scheduler adequate for the Phase 4 target workload, so Phase 4 closes as a docs-only proof with no production change to `app/graph/nodes/dag_merger.py`.

## Rationale

- The roadmap threshold was a realistic `10+` recipe / `50+` step run. The measured `12` recipe / `60` step workload stays well below any concerning latency.
- `_find_stovetop_slot()` is visible in the profile, but not at a level that justifies risk in a correctness-sensitive scheduler.
- The remaining cost is mostly ordinary Python overhead, sorting, NetworkX traversals, and Pydantic bookkeeping rather than the originally suspected overlap-scan pathology.
