---
phase: 04
slug: performance
status: ready
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-08
---

# Phase 04 - Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + standalone Python benchmark |
| **Config file** | `pytest.ini` |
| **Quick run command** | `./.venv/bin/python .planning/phases/04-performance/benchmark_dag_merger.py` |
| **Full suite command** | `./.venv/bin/python -m pytest -q tests/test_kitchen_edge_cases.py tests/test_phase6_unit.py -o addopts=''` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After Task 1:** Run `./.venv/bin/python .planning/phases/04-performance/benchmark_dag_merger.py`
- **After Task 2:** Run `./.venv/bin/python -m pytest -q tests/test_kitchen_edge_cases.py tests/test_phase6_unit.py -o addopts=''`
- **Before `/gsd-verify-work`:** The scheduler regression gate must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | PERF-03 | T-04-01 | profiling artifact captures the real `_merge_dags()` path at `10+` recipes / `50+` steps before any optimization decision | benchmark | `./.venv/bin/python .planning/phases/04-performance/benchmark_dag_merger.py` | ✅ | ⬜ pending |
| 04-01-02 | 01 | 1 | PERF-03 | T-04-02 | scheduler behavior remains unchanged unless a measured hot path requires a narrow fix, and the scheduler regression suite stays green | targeted pytest | `./.venv/bin/python -m pytest -q tests/test_kitchen_edge_cases.py tests/test_phase6_unit.py -o addopts=''` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] Existing scheduler tests already cover the required regression seams.
- [x] Existing local virtualenv can run the benchmark helper and pytest gate.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Final decision stays evidence-backed | PERF-03 | Whether the phase closes docs-only or with a hot-path fix depends on interpreting the captured profile, not a single threshold assert | Read `04-PROFILE.md` and confirm it includes the scaling table, top cProfile frames, and an explicit `Decision` section |
| Any code change stays narrow | PERF-03 | Diff scope matters more than just test pass/fail for this phase | If `app/graph/nodes/dag_merger.py` changes, inspect the diff and confirm only the measured hot path changed |

---

## Validation Sign-Off

- [x] All tasks have automated verification commands
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all missing references
- [x] No watch-mode flags
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-04-08
