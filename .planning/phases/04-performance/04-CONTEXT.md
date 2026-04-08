# Phase 4: Performance - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 4 is limited to scheduler performance investigation and any narrowly justified fix that follows from that investigation. The phase does not add new scheduling capabilities or change pipeline topology. It profiles the current `dag_merger` slot-finding path under realistic multi-recipe load, then either:
- documents that the existing implementation is already adequate, or
- lands the smallest safe optimization needed to close the confirmed bottleneck.

Kitchen edge-case behavior and existing scheduling outputs remain the regression gate for this phase.

</domain>

<decisions>
## Implementation Decisions

### Profiling-first approach
- **D-01:** Phase 4 starts with measurement, not refactoring. No scheduler optimization ships unless profiling shows the hot path still exhibits problematic growth under realistic load.
- **D-02:** The profiling target is the real `dag_merger` slot-finding path for menus at roughly `10+` recipes and `50+` steps, matching the roadmap success criteria rather than a synthetic micro-benchmark only.

### Scope of any optimization
- **D-03:** Treat the existing `_IntervalIndex` implementation as the first thing to evaluate before proposing any replacement. If it already resolves the previously suspected O(n²) path, the phase can complete as a documentation/proof phase with no production code change.
- **D-04:** If a bottleneck remains, optimize the narrowest confirmed hot path in `app/graph/nodes/dag_merger.py` rather than broad scheduler architecture changes.

### Regression and safety contract
- **D-05:** `tests/test_kitchen_edge_cases.py` and the existing scheduler-heavy assertions in `tests/test_phase6_unit.py` are mandatory regression gates for this phase.
- **D-06:** Any performance change must preserve current scheduling behavior and fixture outputs; improved speed is allowed, behavioral drift is not.

### Evidence and output
- **D-07:** The phase should record profiling evidence in phase artifacts so the decision to change code or not change code is auditable later.

### the agent's Discretion
- Exact profiling harness shape, measurement commands, and dataset construction
- Whether the proof is best captured with a focused benchmark helper, targeted pytest coverage, or artifact notes from repeatable command runs
- Exact implementation of a narrow optimization if profiling proves one is still needed

</decisions>

<specifics>
## Specific Ideas

- No product-facing changes are expected from this phase unless they are an unavoidable side effect of making scheduler internals measurably faster.
- "Profile before investing" is already a locked project decision in STATE and remains the governing rule for this phase.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase definition
- `.planning/ROADMAP.md` — defines Phase 4 as a profiling-gated scheduler performance phase with a docs-only success path if `_IntervalIndex` already solves the issue
- `.planning/REQUIREMENTS.md` — maps the phase to `PERF-03`
- `.planning/STATE.md` — carries forward the locked project decision to profile before implementing interval-based scheduler changes

### Code and concern surface
- `.planning/codebase/CONCERNS.md` — records the original scheduler O(n²) concern and its suspected hotspot in `dag_merger.py`
- `app/graph/nodes/dag_merger.py` — current scheduling implementation, including `_IntervalIndex` and slot-finding logic

### Regression gates
- `tests/test_kitchen_edge_cases.py` — explicit regression gate called out in the roadmap for Phase 4
- `tests/test_phase6_unit.py` — broad scheduler and fixture-level correctness coverage that any performance work must preserve

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `app/graph/nodes/dag_merger.py::_IntervalIndex` — existing interval helper already present in the scheduler and likely central to the profiling question
- `tests/test_kitchen_edge_cases.py` — focused edge-case scheduler regressions for zero burners, missing config, and malformed descriptors
- `tests/test_phase6_unit.py` — large body of scheduler correctness tests that can detect subtle regressions if the hot path is changed

### Established Patterns
- Scheduler changes are validated primarily through direct function tests of `_merge_dags()` and related helpers rather than end-to-end pipeline runs
- Previous hardening phases favored narrow fixes plus targeted tests, then a full `pytest -m "not integration" -v` regression gate

### Integration Points
- `_find_earliest_start()` in `app/graph/nodes/dag_merger.py` is the main slot-finding seam for pooled resources
- Burner and equipment interval tracking in `dag_merger.py` already use `_IntervalIndex`, so profiling must distinguish between solved paths and any remaining linear scans

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 04-performance*
*Context gathered: 2026-04-08*
