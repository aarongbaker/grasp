"""
graph/nodes/dag_merger.py
Real DAG merger — Phase 6. Greedy list scheduler with critical-path priority.

Reads recipe_dags + validated_recipes + kitchen_config from GRASPState.
Merges per-recipe DAGs into a single resource-aware, time-optimized
MergedDAG with absolute step timings.

Resource model (V1 — pure resource pools, all independent):
  HANDS:    capacity = 1 (exclusive)
  STOVETOP: capacity = max_burners (default 4)
  OVEN:     capacity = 1 per oven (has_second_oven → 2)
  PASSIVE:  capacity = unlimited (always parallelisable)

Scheduling priority (deterministic):
  1. Critical-path length (descending) — longest remaining work first
  2. recipe_slug (ascending alphabetical) — tiebreaker
  3. step_id (ascending) — within-recipe ordering

IDEMPOTENCY: Returns merged_dag as a single dict (replace semantics).

Mockable seam:
  _merge_dags()  — pure algorithmic function, no external deps
"""

import bisect
import logging
from dataclasses import dataclass, field
import re
from typing import Any, Optional

from pydantic import ValidationError

import networkx as nx
from pydantic import TypeAdapter

from app.models.enums import ErrorType, Resource
from app.models.pipeline import GRASPState
from app.models.recipe import ValidatedRecipe
from app.models.scheduling import MergedDAG, OneOvenConflictSummary, RecipeDAG, ScheduledStep
from app.models.user import BurnerDescriptor

logger = logging.getLogger(__name__)


class ResourceConflictError(Exception):
    """Raised when the scheduler cannot find a valid time slot."""

    # We carry structured metadata alongside the message so that
    # dag_merger_node can surface rich conflict details (temps, recipes, remediation)
    # without having to re-parse the error string. The LangGraph router uses
    # ErrorType.RESOURCE_CONFLICT to decide whether to retry generation.
    def __init__(self, message: str, metadata: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.metadata = metadata or {}


@dataclass
class _StepInfo:
    """Internal representation joining DAG edges with step details."""

    # step_id is globally unique across the merged graph (prefixed with recipe_slug in dag_builder)
    step_id: str
    recipe_name: str
    recipe_slug: str
    description: str
    resource: Resource
    duration_minutes: int
    # duration_max is the pessimistic bound for slack / worst-case timeline computation
    duration_max: Optional[int] = None
    required_equipment: list[str] = field(default_factory=list)
    can_be_done_ahead: bool = False
    prep_ahead_window: Optional[str] = None
    prep_ahead_notes: Optional[str] = None
    # depends_on is rebuilt from the authoritative edge list after merging to keep it consistent
    depends_on: list[str] = field(default_factory=list)
    # critical_path_length is computed once and used as the primary scheduling priority key
    critical_path_length: int = 0
    # merged_from tracks which original step_ids were collapsed into this node (for merged prep)
    merged_from: list[str] = field(default_factory=list)  # step_ids consolidated into this merged node
    # allocation maps recipe_name → quantity string for merged prep steps (e.g., "2 cups" for Recipe A)
    allocation: dict[str, str] = field(default_factory=dict)  # recipe_name → quantity breakdown
    # oven_temp_f is carried through for oven conflict detection (15°F tolerance per V1 spec)
    oven_temp_f: Optional[int] = None  # Fahrenheit temperature for oven steps
    # stovetop_heat_f is a burner-local preference signal, not a pool-level constraint
    stovetop_heat_f: Optional[int] = None  # Optional stovetop heat preference signal for burner continuity


@dataclass
class _OvenInterval:
    """Track oven usage intervals with temperature for conflict detection."""

    # We need both temporal bounds (for overlap math) and temperature (for compatibility check)
    start: int
    end: int
    temp_f: Optional[int]
    recipe_name: str
    step_id: str
    # course is used by _involves_entree() to relax conflict classification:
    # entree steps are treated as anchors and their conflicts are always resequence_required
    # rather than irreconcilable, since the entree timing is the least negotiable.
    course: Optional[str] = None  # recipe course — used for entree-anchor conflict classification


@dataclass(frozen=True)
class _BurnerSlot:
    """Stable stovetop slot metadata derived from kitchen config or fallback numbering."""

    # frozen=True makes this hashable and prevents accidental mutation during scheduling.
    # The scheduler uses burner_id as the stable dictionary key throughout.
    burner_id: str
    position: Optional[str] = None
    size: Optional[str] = None
    label: Optional[str] = None

    def to_descriptor(self) -> BurnerDescriptor:
        # Converts internal scheduling representation to the public model that gets
        # persisted on ScheduledStep — keeps internal/external types cleanly separated.
        return BurnerDescriptor(
            burner_id=self.burner_id,
            position=self.position,
            size=self.size,
            label=self.label,
        )


# TypeAdapter for batch-validating the raw burner list from kitchen_config.
# Defined at module level to avoid reconstructing the adapter on every call.
_BURNER_DESCRIPTOR_ADAPTER = TypeAdapter(list[BurnerDescriptor])


def _compute_critical_paths(
    steps: list[_StepInfo],
    all_edges: list[tuple[str, str]],
) -> dict[str, int]:
    """
    Bottom-up critical path: duration of longest path from each step to any
    sink node within its recipe. Used for scheduling priority.
    """
    # Use duration_max (pessimistic bound) for critical path so that the scheduler
    # prioritises steps that are risky to delay, not just nominally long ones.
    dur = {s.step_id: (s.duration_max or s.duration_minutes) for s in steps}

    G = nx.DiGraph()
    G.add_nodes_from(dur.keys())
    G.add_edges_from(all_edges)

    cp: dict[str, int] = {}
    # Topological order guarantees all successors are processed before predecessors
    # in reversed order, enabling the bottom-up DP without recursion.
    for step_id in reversed(list(nx.topological_sort(G))):
        successors = list(G.successors(step_id))
        if not successors:
            # Sink node: critical path length is just this step's own duration
            cp[step_id] = dur[step_id]
        else:
            # Non-sink: this step plus the longest downstream chain
            cp[step_id] = dur[step_id] + max(cp[s] for s in successors)

    # Handle isolated nodes (no edges) — shouldn't happen but be safe
    for s in steps:
        if s.step_id not in cp:
            cp[s.step_id] = s.duration_minutes

    return cp


def _is_cooking_step(resource: Resource) -> bool:
    """Return True for active cooking resources (STOVETOP, OVEN)."""
    # PASSIVE and HANDS steps are not subject to finish-together offset logic —
    # only active heat steps need to finish in sync. This distinction is central
    # to the serving_time scheduling contract.
    return resource in (Resource.STOVETOP, Resource.OVEN)


def _compute_recipe_cooking_durations(
    all_steps: list[_StepInfo],
    all_edges: list[tuple[str, str]],
) -> dict[str, int]:
    """
    Compute total cooking time per recipe (STOVETOP + OVEN steps only).

    Returns recipe_name → total cooking minutes. Follows the critical path
    within each recipe to avoid double-counting parallel cooking steps.
    """
    # Group steps by recipe
    steps_by_recipe: dict[str, list[_StepInfo]] = {}
    for step in all_steps:
        steps_by_recipe.setdefault(step.recipe_name, []).append(step)

    # For each recipe, compute the critical path length considering only cooking steps
    result: dict[str, int] = {}

    for recipe_name, steps in steps_by_recipe.items():
        # Filter edges to this recipe — cross-recipe edges don't affect intra-recipe cooking time
        recipe_step_ids = {s.step_id for s in steps}
        recipe_edges = [(src, dst) for src, dst in all_edges if src in recipe_step_ids and dst in recipe_step_ids]

        # Build graph for this recipe
        G = nx.DiGraph()
        G.add_nodes_from(recipe_step_ids)
        G.add_edges_from(recipe_edges)

        # Compute critical path length for cooking steps only
        # Duration is only counted for STOVETOP/OVEN steps
        cooking_dur = {
            s.step_id: s.duration_minutes if _is_cooking_step(s.resource) else 0 for s in steps
        }

        # Bottom-up critical path: sum of cooking durations along longest path.
        # Non-cooking steps contribute 0, acting as passthrough nodes in the DAG.
        cp: dict[str, int] = {}
        for step_id in reversed(list(nx.topological_sort(G))):
            successors = list(G.successors(step_id))
            if not successors:
                cp[step_id] = cooking_dur[step_id]
            else:
                cp[step_id] = cooking_dur[step_id] + max(cp[s] for s in successors)

        # Handle isolated nodes
        for s in steps:
            if s.step_id not in cp:
                cp[s.step_id] = cooking_dur[s.step_id]

        # Recipe cooking duration = max critical path from any root
        roots = [s.step_id for s in steps if G.in_degree(s.step_id) == 0]
        if roots:
            result[recipe_name] = max(cp[root] for root in roots)
        else:
            # Fallback: sum of all cooking step durations (shouldn't happen)
            result[recipe_name] = sum(cooking_dur.values())

    return result


def _compute_finish_together_offsets(
    all_steps: list[_StepInfo],
    all_edges: list[tuple[str, str]],
    serving_time: str | None,
) -> dict[str, int]:
    """
    Compute per-recipe cooking start offsets so all recipes finish together.

    If serving_time is None (ASAP mode), returns empty dict — no offsets applied.

    Otherwise:
    1. Compute cooking duration per recipe using critical path analysis
    2. Find the max cooking duration (anchor recipe)
    3. For each recipe: offset = max_cooking - this_recipe_cooking

    The offset is applied only to cooking steps (STOVETOP/OVEN), not prep steps.

    Returns recipe_name → offset in minutes. Anchor recipe gets 0, shorter
    recipes get positive offsets (delay start of cooking to finish together).
    """
    # ASAP mode: no finish-together scheduling
    if serving_time is None:
        return {}

    # Compute cooking durations per recipe
    cooking_durations = _compute_recipe_cooking_durations(all_steps, all_edges)

    if not cooking_durations:
        return {}

    # Find the anchor (longest cooking) recipe — it starts immediately, sets the floor
    max_cooking = max(cooking_durations.values())

    # Offset = how long the anchor cooks before this recipe should start cooking.
    # Shorter recipes get a positive delay so their cooking finishes at the same moment.
    offsets: dict[str, int] = {}
    for recipe_name, duration in cooking_durations.items():
        offsets[recipe_name] = max_cooking - duration

    return offsets


def _detect_resource_warnings(
    scheduled_steps: list[ScheduledStep],
    finish_offsets: dict[str, int],
    capacities: dict[Resource, float],
) -> list[str]:
    """
    Detect when resource constraints caused recipes to finish later than intended.

    Compares each recipe's actual cooking end time to the anchor's cooking end time.
    If a recipe's cooking ends >20 min after the anchor, it means resource constraints
    (typically oven capacity) forced delays despite finish-together scheduling.

    Args:
        scheduled_steps: The final scheduled steps from the merger
        finish_offsets: The finish-together offsets (empty dict in ASAP mode)
        capacities: Resource capacity configuration (used for warning messages)

    Returns:
        List of user-friendly warning strings
    """
    # No warnings in ASAP mode (no finish-together intent)
    if not finish_offsets:
        return []

    # Find cooking steps per recipe and their end times
    # A cooking step is STOVETOP or OVEN
    cooking_ends_by_recipe: dict[str, int] = {}
    cooking_resource_by_recipe: dict[str, Resource] = {}

    for step in scheduled_steps:
        if not _is_cooking_step(step.resource):
            continue

        # Track the latest cooking end per recipe — that's when the recipe "finishes cooking"
        recipe = step.recipe_name
        if recipe not in cooking_ends_by_recipe or step.end_at_minute > cooking_ends_by_recipe[recipe]:
            cooking_ends_by_recipe[recipe] = step.end_at_minute
            cooking_resource_by_recipe[recipe] = step.resource

    if not cooking_ends_by_recipe:
        return []

    # Find the anchor recipe (the one with offset=0, i.e., longest cooking)
    anchor_recipes = [name for name, offset in finish_offsets.items() if offset == 0]
    if not anchor_recipes:
        return []

    # Get anchor's cooking end time (there may be multiple anchors with equal duration)
    anchor_end_times = [cooking_ends_by_recipe.get(name, 0) for name in anchor_recipes]
    anchor_end = max(anchor_end_times) if anchor_end_times else 0

    # Check each non-anchor recipe for significant delays caused by resource contention
    warnings: list[str] = []
    delay_threshold = 20  # minutes — small delays are noise; 20+ min is actionable

    for recipe_name, cooking_end in cooking_ends_by_recipe.items():
        # Skip anchor recipes
        if recipe_name in anchor_recipes:
            continue

        delay = cooking_end - anchor_end
        if delay > delay_threshold:
            resource = cooking_resource_by_recipe.get(recipe_name, Resource.OVEN)
            resource_name = resource.value.lower()
            capacity = capacities.get(resource, 1)

            # Round delay to nearest 5 minutes for cleaner messaging
            delay_rounded = round(delay / 5) * 5

            # Determine suggestion based on resource type — oven capacity is usually the culprit
            if resource == Resource.OVEN:
                if capacity == 1:
                    suggestion = f"Consider starting {recipe_name} earlier if you have a second oven."
                else:
                    suggestion = "You may need additional oven capacity for simultaneous cooking."
            elif resource == Resource.STOVETOP:
                suggestion = f"Consider timing {recipe_name} to start earlier, waiting for a burner to free up, or adding burners."
            else:
                suggestion = f"Consider adjusting timing for {recipe_name}."

            # Find anchor recipe name for message (use first anchor for simplicity)
            anchor_name = anchor_recipes[0]

            if resource == Resource.STOVETOP:
                warnings.append(
                    f"{recipe_name}'s stovetop cooking will finish ~{delay_rounded} minutes "
                    f"after {anchor_name} because all burners are occupied at the intended start time. {suggestion}"
                )
            else:
                warnings.append(
                    f"{recipe_name}'s {resource_name} cooking will finish ~{delay_rounded} minutes "
                    f"after {anchor_name} due to {resource_name} capacity. {suggestion}"
                )

    return warnings


class _IntervalIndex:
    """Sorted interval index with O(log n) overlap counting."""

    # This is the core data structure enabling fast resource conflict detection.
    # Two separate sorted lists (starts and ends) allow O(log n) bisect operations
    # instead of iterating all intervals for every candidate time slot.
    #
    # The overlap count formula works because:
    #   - "intervals that started before window_end" minus "intervals that ended by window_start"
    #   = intervals currently active during the window.
    # This exploits the sorted property of both lists for two binary searches.

    def __init__(self) -> None:
        self._starts: list[int] = []
        self._ends: list[int] = []

    def add(self, start: int, end: int) -> None:
        # insort keeps the lists in sorted order after each insertion — O(log n).
        # This is why we use two separate lists instead of a list of tuples:
        # we need to binary-search starts and ends independently.
        bisect.insort(self._starts, start)
        bisect.insort(self._ends, end)

    def count_overlapping(self, window_start: int, window_end: int) -> int:
        """Count intervals overlapping [window_start, window_end)."""
        # bisect_left on starts: count of intervals that started before window_end
        # bisect_right on ends: count of intervals that already ended by window_start
        # Their difference = count of intervals overlapping the window
        started_before_end = bisect.bisect_left(self._starts, window_end)
        ended_before_start = bisect.bisect_right(self._ends, window_start)
        return started_before_end - ended_before_start

    def min_end_after(self, t: int) -> int | None:
        """Return the smallest end value > t, or None."""
        # Used to find the next "slot opening" after a full-capacity window.
        # We advance the candidate to the earliest moment when capacity decreases.
        idx = bisect.bisect_right(self._ends, t)
        return self._ends[idx] if idx < len(self._ends) else None

    def intervals(self) -> list[tuple[int, int]]:
        """Return sorted (start, end) pairs for utilisation output."""
        # Re-zip the two sorted lists into pairs for the resource_utilisation output.
        # The zip is valid because insort keeps relative insertion order consistent
        # only if starts and ends are paired — this works because each add() inserts one of each.
        pairs = list(zip(self._starts, self._ends))
        pairs.sort()
        return pairs

    def __len__(self) -> int:
        return len(self._starts)


def _format_oven_conflict_message(
    recipe_a: str,
    temp_a: int,
    recipe_b: str,
    temp_b: int,
    start_minute: int,
    end_minute: int,
) -> str:
    """Format R026-compliant error message for oven temperature conflicts."""
    # Convert minutes to HH:MM format
    start_h, start_m = divmod(start_minute, 60)
    end_h, end_m = divmod(end_minute, 60)
    start_time = f"{start_h}:{start_m:02d}"
    end_time = f"{end_h}:{end_m:02d}"

    return (
        f"Oven temperature conflict: {recipe_a} needs {temp_a}°F while "
        f"{recipe_b} needs {temp_b}°F from {start_time} to {end_time}. "
        f"You need a second oven or different recipes."
    )


def _involves_entree(a: _OvenInterval, b: _OvenInterval) -> bool:
    """Return True if either oven interval belongs to the entree recipe."""
    # Entrees are the timing anchor for a dinner party — their oven windows
    # are non-negotiable. If an entree is involved in a conflict, we downgrade
    # from "irreconcilable" to "resequence_required" because the entree slot
    # cannot be moved, but side dishes can often be staggered.
    return a.course == "entree" or b.course == "entree"


def _build_one_oven_conflict_metadata(
    oven_intervals: list[_OvenInterval],
    has_second_oven: bool,
    tolerance_f: int = 15,
    *,
    treat_overlap_as_irreconcilable: bool = False,
) -> OneOvenConflictSummary:
    """Summarize one-oven temperature feasibility from oven demand windows.

    Classification is conservative and intentionally separates two views:
    - already-scheduled windows can be reported as resequence_required when the
      scheduler proved a single-oven ordering exists
    - intended overlapping windows can be reported as irreconcilable when a
      finish-together request or other upstream timing contract makes the overlap
      a hard requirement
    - missing temperatures stay compatible so we do not invent certainty from
      sparse metadata
    """
    # Sentinel dict matches the Pydantic model structure for model_validate at the end.
    # Using a dict here (not constructing OneOvenConflictSummary directly) lets us
    # progressively patch only the fields that change for each classification tier.
    summary: dict[str, Any] = {
        "classification": "compatible",
        "tolerance_f": tolerance_f,
        "has_second_oven": has_second_oven,
        "temperature_gap_f": None,
        "blocking_recipe_names": [],
        "affected_step_ids": [],
        "remediation": {
            "requires_resequencing": False,
            "suggested_actions": [],
            "delaying_recipe_names": [],
            "blocking_recipe_names": [],
            "notes": None,
        },
    }

    # Second oven eliminates all single-oven temperature conflicts by definition.
    # With fewer than 2 oven steps, there's nothing to conflict.
    if has_second_oven or len(oven_intervals) < 2:
        return OneOvenConflictSummary.model_validate(summary)

    max_gap: Optional[int] = None
    # We track the most severe pair found in each tier, then report only the worst.
    resequence_pair: Optional[tuple[_OvenInterval, _OvenInterval, int]] = None
    irreconcilable_pair: Optional[tuple[_OvenInterval, _OvenInterval, int]] = None

    for idx, interval_a in enumerate(oven_intervals):
        if interval_a.temp_f is None:
            continue  # Can't classify without temperature data — stay compatible
        for interval_b in oven_intervals[idx + 1 :]:
            if interval_b.temp_f is None:
                continue
            gap = abs(interval_a.temp_f - interval_b.temp_f)
            # Track maximum temperature gap across all pairs for reporting
            if max_gap is None or gap > max_gap:
                max_gap = gap

            # Within tolerance: ovens can share heat without issue — skip
            if gap <= tolerance_f:
                continue

            # Check temporal overlap: open-interval logic [start, end)
            overlaps = interval_a.start < interval_b.end and interval_b.start < interval_a.end
            if overlaps:
                # An actual simultaneous overlap with incompatible temps.
                # If treat_overlap_as_irreconcilable (finish-together mode), and no entree anchor
                # softens it, this is a hard conflict requiring a second oven.
                if irreconcilable_pair is None:
                    irreconcilable_pair = (interval_a, interval_b, gap)
                # Still track resequence_pair in case entree-anchor logic applies
                if (not treat_overlap_as_irreconcilable or _involves_entree(interval_a, interval_b)) and resequence_pair is None:
                    earlier, later = (interval_a, interval_b)
                    if interval_b.start < interval_a.start:
                        earlier, later = interval_b, interval_a
                    resequence_pair = (earlier, later, gap)
            elif resequence_pair is None:
                # Non-overlapping but incompatible temps — could conflict if rescheduled.
                # The scheduler proved they don't actually overlap, so resequencing suffices.
                earlier, later = (interval_a, interval_b)
                if interval_b.start < interval_a.start:
                    earlier, later = interval_b, interval_a
                resequence_pair = (earlier, later, gap)

    if max_gap is not None:
        summary["temperature_gap_f"] = max_gap

    # Irreconcilable path: overlapping windows that can't be collapsed,
    # unless an entree anchor is involved (which softens the classification).
    if irreconcilable_pair is not None and treat_overlap_as_irreconcilable and not _involves_entree(*irreconcilable_pair[:2]):
        left, right, gap = irreconcilable_pair
        # Compute the actual overlap window for the error message timestamp
        overlap_start = max(left.start, right.start)
        overlap_end = min(left.end, right.end)
        blocking_recipe_names = [left.recipe_name, right.recipe_name]
        affected_step_ids = [left.step_id, right.step_id]
        summary.update(
            {
                "classification": "irreconcilable",
                "temperature_gap_f": gap,
                "blocking_recipe_names": blocking_recipe_names,
                "affected_step_ids": affected_step_ids,
                "remediation": {
                    "requires_resequencing": False,
                    "suggested_actions": ["Use a second oven or change recipes."],
                    "delaying_recipe_names": [],
                    "blocking_recipe_names": blocking_recipe_names,
                    "notes": _format_oven_conflict_message(
                        left.recipe_name,
                        left.temp_f or 0,
                        right.recipe_name,
                        right.temp_f or 0,
                        overlap_start,
                        overlap_end,
                    ),
                },
            }
        )
        return OneOvenConflictSummary.model_validate(summary)

    # Resequence path: scheduler found a feasible single-oven ordering,
    # but the user should know they need to stagger these oven windows.
    if resequence_pair is not None:
        blocker, delayed, gap = resequence_pair
        summary.update(
            {
                "classification": "resequence_required",
                "temperature_gap_f": gap,
                "blocking_recipe_names": [blocker.recipe_name, delayed.recipe_name],
                "affected_step_ids": [blocker.step_id, delayed.step_id],
                "remediation": {
                    "requires_resequencing": True,
                    "suggested_actions": [f"Bake {delayed.recipe_name} after {blocker.recipe_name} finishes."],
                    "delaying_recipe_names": [delayed.recipe_name],
                    "blocking_recipe_names": [blocker.recipe_name],
                    "notes": "Single-oven schedule remains feasible by staging incompatible oven temperatures into separate windows.",
                },
            }
        )

    return OneOvenConflictSummary.model_validate(summary)


def _build_planned_oven_intervals(
    all_steps: list[_StepInfo],
    all_edges: list[tuple[str, str]],
    finish_offsets: dict[str, int],
    course_by_recipe: Optional[dict[str, Optional[str]]] = None,
) -> list[_OvenInterval]:
    """Build conservative oven demand windows before resource-capacity placement.

    This captures when oven steps *would* want to run from dependency timing plus
    finish-together offsets, so one-oven classification can tell the difference
    between a menu that merely needs staging and one whose requested windows are
    fundamentally incompatible.
    """
    if not all_steps:
        return []

    by_step_id = {step.step_id: step for step in all_steps}
    earliest_end: dict[str, int] = {}

    # Topological traversal computes the earliest possible end time for each step
    # assuming infinite resource capacity — this is the "ideal" oven demand window.
    graph = nx.DiGraph()
    graph.add_nodes_from(by_step_id.keys())
    graph.add_edges_from(all_edges)

    for step_id in nx.topological_sort(graph):
        step = by_step_id[step_id]
        # Earliest start is the max end time of all predecessors (zero for roots)
        earliest_start = max((earliest_end[dep] for dep in step.depends_on), default=0)
        # Apply finish-together offset to cooking steps: delay their ideal start
        if _is_cooking_step(step.resource) and step.recipe_name in finish_offsets:
            earliest_start = max(earliest_start, finish_offsets[step.recipe_name])
        earliest_end[step_id] = earliest_start + step.duration_minutes

    # Build the oven interval list from only OVEN steps
    planned: list[_OvenInterval] = []
    for step in all_steps:
        if step.resource != Resource.OVEN:
            continue
        end = earliest_end.get(step.step_id)
        if end is None:
            continue
        start = end - step.duration_minutes
        planned.append(
            _OvenInterval(
                start=start,
                end=end,
                temp_f=step.oven_temp_f,
                recipe_name=step.recipe_name,
                step_id=step.step_id,
                course=(course_by_recipe or {}).get(step.recipe_name),
            )
        )
    return planned


def _find_earliest_start(
    resource: Resource,
    duration: int,
    earliest_from_deps: int,
    resource_intervals: dict[Resource, "_IntervalIndex"],
    capacities: dict[Resource, float],
    oven_intervals: Optional[list["_OvenInterval"]] = None,
    oven_temp_f: Optional[int] = None,
    step_recipe_name: Optional[str] = None,
) -> int:
    """
    Find the earliest start time for a step that satisfies resource constraints.
    PASSIVE steps always start at earliest_from_deps (no capacity limit).

    For OVEN steps with temperature conflicts:
    - If there's spare capacity, incompatible temps don't matter (use different ovens)
    - If capacity is full and all slots have incompatible temps, advance time
    - Only raise error if we can't find a valid slot after many iterations
    """
    # PASSIVE is always schedulable — no resource consumption, infinite capacity
    if resource == Resource.PASSIVE:
        return earliest_from_deps

    index = resource_intervals[resource]
    cap = capacities[resource]
    candidate = earliest_from_deps
    # 15°F matches the OneOvenConflictSummary tolerance — both must agree
    TEMP_TOLERANCE_F = 15

    for iteration in range(10_000):  # safety valve — prevents infinite loops on pathological inputs
        window_end = candidate + duration
        overlap_count = index.count_overlapping(candidate, window_end)

        # Check if there's capacity available
        if overlap_count < cap:
            # Spare capacity exists — we can schedule here without any further checks.
            # For OVEN with a second oven, incompatible temps don't matter since we
            # can physically use the other oven.
            return candidate

        # Capacity is full - check if we can wait for a compatible slot
        # For oven steps with temps, see if any intervals are compatible
        if resource == Resource.OVEN and oven_intervals and oven_temp_f is not None:
            # Partition overlapping intervals into compatible vs. incompatible by temperature
            compatible_intervals = []
            incompatible_intervals = []

            for interval in oven_intervals:
                if interval.start < window_end and interval.end > candidate:
                    if interval.temp_f is not None:
                        temp_diff = abs(oven_temp_f - interval.temp_f)
                        if temp_diff <= TEMP_TOLERANCE_F:
                            compatible_intervals.append(interval)
                        else:
                            incompatible_intervals.append(interval)

            # If we have compatible intervals, we might be able to "share" with them
            # by waiting for capacity to free up while staying compatible
            # But since capacity is full, we need to advance time regardless

            # Prefer advancing past incompatible intervals first — this maximises the
            # chance that the next candidate overlaps only with compatible oven steps.
            if incompatible_intervals:
                next_end = min(i.end for i in incompatible_intervals)
            else:
                next_end = index.min_end_after(candidate)
                if next_end is None:
                    return candidate

            candidate = next_end
            continue

        # Non-oven (or oven without temp metadata): advance past the earliest-ending interval
        next_end = index.min_end_after(candidate)
        if next_end is None:
            return candidate  # shouldn't happen, but be safe
        candidate = next_end

    # Exhausted 10k iterations — almost certainly a genuine temperature deadlock.
    # Check the current window for an irreconcilable oven conflict and raise with metadata
    # so the LangGraph router can surface a structured error rather than a generic crash.
    if resource == Resource.OVEN and oven_intervals and oven_temp_f is not None:
        window_end = candidate + duration
        for interval in oven_intervals:
            if interval.start < window_end and interval.end > candidate:
                if interval.temp_f is not None:
                    temp_diff = abs(oven_temp_f - interval.temp_f)
                    if temp_diff > TEMP_TOLERANCE_F:
                        message = _format_oven_conflict_message(
                            step_recipe_name or "Recipe",
                            oven_temp_f,
                            interval.recipe_name,
                            interval.temp_f,
                            max(candidate, interval.start),
                            min(window_end, interval.end),
                        )
                        # Metadata mirrors OneOvenConflictSummary schema so the node can
                        # validate it directly without re-parsing the error string
                        metadata = {
                            "classification": "irreconcilable",
                            "tolerance_f": TEMP_TOLERANCE_F,
                            "has_second_oven": False,
                            "temperature_gap_f": temp_diff,
                            "blocking_recipe_names": [step_recipe_name or "Recipe", interval.recipe_name],
                            "affected_step_ids": [interval.step_id],
                            "remediation": {
                                "requires_resequencing": False,
                                "suggested_actions": ["Use a second oven or change recipes."],
                                "delaying_recipe_names": [],
                                "blocking_recipe_names": [step_recipe_name or "Recipe", interval.recipe_name],
                                "notes": message,
                            },
                        }
                        raise ResourceConflictError(message, metadata=metadata)

    raise ResourceConflictError(f"Cannot schedule step: resource {resource.value} exhausted after 10,000 iterations")


def _build_burner_slots(kitchen_config: dict[str, Any]) -> list[_BurnerSlot]:
    """Return stable stovetop slots from explicit descriptors or fallback max_burners numbering."""
    raw_burners = kitchen_config.get("burners") or []
    slots: list[_BurnerSlot] = []

    if raw_burners:
        try:
            # Validate the raw burner list against BurnerDescriptor models.
            # If the kitchen_config has malformed burner data, we fall through
            # to the numbered fallback rather than crashing the whole pipeline.
            descriptors = _BURNER_DESCRIPTOR_ADAPTER.validate_python(raw_burners)
        except ValidationError:
            logger.warning("Malformed burner descriptors in kitchen_config; falling back to max_burners numbering")
        else:
            for descriptor in descriptors:
                slots.append(
                    _BurnerSlot(
                        burner_id=descriptor.burner_id,
                        position=descriptor.position,
                        size=descriptor.size,
                        label=descriptor.label,
                    )
                )
            return slots

    # Fallback: create anonymised burner slots numbered burner_1..burner_N.
    # This ensures the stovetop scheduling model always has stable slot identities
    # to work with, even when kitchen_config has no burner descriptors.
    burner_count = max(int(kitchen_config.get("max_burners", 4) or 0), 0)
    for index in range(burner_count):
        burner_number = index + 1
        slots.append(
            _BurnerSlot(
                burner_id=f"burner_{burner_number}",
                label=f"Burner {burner_number}",
            )
        )
    return slots


def _extract_stovetop_heat_f(description: str) -> Optional[int]:
    """Extract an optional stovetop heat preference in Fahrenheit from step text.

    This is intentionally narrow: S02 treats stovetop heat as a local burner-selection
    preference signal, not a pool-level feasibility rule. If no explicit heat signal is
    present, placement falls back to suitability + stable burner identity.
    """
    if not description:
        return None

    # Pattern 1: explicit "heat_f: 350" or "stovetop heat: 350" style annotation
    match = re.search(r"(?:stovetop[_\s-]*)?heat(?:[_\s-]*f)?\s*[:=]?\s*(\d{2,3})", description, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # Pattern 2: "350 °F" temperature annotation anywhere in the description
    temp_match = re.search(r"(\d{2,3})\s*°\s*f", description, re.IGNORECASE)
    if temp_match:
        return int(temp_match.group(1))

    return None


# Maps size class strings to numeric ranks for comparison arithmetic.
# lower rank = smaller burner; used in _burner_size_score to determine fit quality.
_SIZE_CLASS_RANK = {"small": 0, "medium": 1, "large": 2}


def _infer_required_burner_size(step: _StepInfo) -> Optional[str]:
    """Infer a narrow burner-size preference from existing structured/local recipe signals.

    S02 deliberately does not invent a general hardware-capability engine. We only consume
    signals already present in step text/allocation metadata and map them into the coarse
    burner classes already supported by kitchen burner descriptors.
    """
    # Concatenate description and allocation values for a single scan pass
    text = " ".join(filter(None, [step.description, " ".join(step.allocation.values())])).lower()

    # Pattern list ordered large → small so we match the strongest signal first.
    # Each pattern must be specific enough to avoid false positives in recipe prose.
    explicit_patterns = [
        (
            "large",
            [
                r"\brequires?\s+(?:a\s+)?large\s+burner\b",
                r"\blarge\s+burner\b",
                r"\blarge\s+pan\b",
                r"\bwide\s+skillet\b",
                r"\bhigh-heat\s+sear\b",
                r"\bsear\b",
            ],
        ),
        (
            "small",
            [
                r"\brequires?\s+(?:a\s+)?small\s+burner\b",
                r"\bsmall\s+burner\b",
                r"\bsmall\s+pan\b",
                r"\bgentle\s+simmer\b",
                r"\blow\s+simmer\b",
            ],
        ),
    ]
    for size, patterns in explicit_patterns:
        if any(re.search(pattern, text) for pattern in patterns):
            return size

    return None


def _burner_size_score(slot: _BurnerSlot, required_size: Optional[str]) -> int:
    """Return a deterministic suitability score; lower is better."""
    # No size requirement: any slot is equally suitable (score 0)
    if required_size is None:
        return 0
    # Slot has no size annotation: neutral fit (score 1) — usable but not ideal
    if slot.size is None:
        return 1

    slot_rank = _SIZE_CLASS_RANK.get(slot.size.lower())
    required_rank = _SIZE_CLASS_RANK.get(required_size.lower())
    if slot_rank is None or required_rank is None:
        return 1  # Unknown size class — treat as neutral
    if slot_rank == required_rank:
        return 0  # Perfect size match
    if slot_rank > required_rank:
        # Slot is bigger than needed — acceptable but not ideal (score = size difference)
        return slot_rank - required_rank
    # Slot is smaller than needed — penalise heavily (100+) to deprioritise strongly
    return 100 + (required_rank - slot_rank)


def _most_recent_stovetop_heat(
    slot: _BurnerSlot,
    burner_history: dict[str, list[tuple[int, Optional[int]]]],
    candidate: int,
) -> Optional[int]:
    """Return the latest known heat recorded for this burner before candidate time."""
    # Scanning in reverse order ensures we find the most recent entry first.
    # The history list is append-only (entries are in chronological end_minute order),
    # so reversing gives us the most recent completed step without sorting.
    history = burner_history.get(slot.burner_id, [])
    for end_minute, heat_f in reversed(history):
        if end_minute <= candidate:
            return heat_f
    return None


def _find_stovetop_slot(
    step: _StepInfo,
    earliest_from_deps: int,
    burner_slots: list[_BurnerSlot],
    burner_intervals: dict[str, _IntervalIndex],
    burner_history: dict[str, list[tuple[int, Optional[int]]]],
) -> tuple[int, _BurnerSlot]:
    """Apply the S02 stovetop placement policy.

    Policy order is intentionally narrow and deterministic:
    1. prefer any suitable burner free at the candidate start time
    2. among suitable free burners, prefer the burner whose most recent assigned
       stovetop heat is closest to the requested ``stovetop_heat_f``
    3. if heat metadata is absent or tied, prefer the most size-appropriate burner
       class when structured/local recipe signals justify it
    4. if still tied, prefer the lowest stable burner identity
    5. if no suitable burner is free at the candidate time, advance to the next
       suitable burner release boundary and retry

    This helper intentionally does *not* perform global stovetop search or treat
    mixed stovetop heats as a pool-level conflict. Availability, heat continuity,
    and size suitability are burner-local signals only.
    """
    if not burner_slots:
        raise ResourceConflictError("Cannot schedule stovetop step: no burner slots configured")

    required_size = _infer_required_burner_size(step)
    requested_heat_f = step.stovetop_heat_f
    candidate = earliest_from_deps

    for _ in range(10_000):  # safety valve
        window_end = candidate + step.duration_minutes
        # Accumulate (sort_key_tuple, slot) pairs for free, suitable burners
        suitable_free: list[tuple[tuple[int, int, str], _BurnerSlot]] = []
        next_release: Optional[int] = None  # earliest moment a suitable burner frees up

        for slot in burner_slots:
            size_score = _burner_size_score(slot, required_size)
            # score ≥ 100 means the slot is too small — hard disqualification
            if size_score >= 100:
                continue

            slot_index = burner_intervals[slot.burner_id]
            if slot_index.count_overlapping(candidate, window_end) == 0:
                # Burner is free at this time window — compute heat affinity score
                recent_heat = _most_recent_stovetop_heat(slot, burner_history, candidate)
                # Heat score: smaller delta = better affinity. 10_000 when no heat data
                # so heat-unaware burners sort behind heat-matched ones but still get used.
                heat_score = abs(recent_heat - requested_heat_f) if (recent_heat is not None and requested_heat_f is not None) else 10_000
                suitable_free.append(((heat_score, size_score, slot.burner_id), slot))
                continue

            # Burner is occupied — note its release time for the advance step
            slot_release = slot_index.min_end_after(candidate)
            if slot_release is not None and (next_release is None or slot_release < next_release):
                next_release = slot_release

        if suitable_free:
            # Sort by (heat_score, size_score, burner_id) — deterministic tiebreaking
            suitable_free.sort(key=lambda item: item[0])
            return candidate, suitable_free[0][1]

        if next_release is None:
            raise ResourceConflictError("Cannot schedule stovetop step: burner release boundary not found")
        # Advance to the next moment when a suitable burner becomes free and retry
        candidate = next_release

    raise ResourceConflictError("Cannot schedule stovetop step: burner allocation exhausted after 10,000 iterations")


def _detect_merge_candidates(
    validated_recipes: list[ValidatedRecipe],
) -> dict[tuple[str, str], list[tuple[str, str, float, str]]]:
    """
    Detect steps that can be merged based on exact ingredient+prep match.

    Returns a dict mapping (ingredient_name, prep_method) → list of (step_id, recipe_name, quantity, unit).
    Only includes tuples with 2+ steps (mergeable).
    """
    # Build index: (ingredient, prep_method) → list of (step_id, recipe_name, quantity, unit)
    candidates: dict[tuple[str, str], list[tuple[str, str, float, str]]] = {}

    for vr in validated_recipes:
        recipe_name = vr.source.source.name
        for step in vr.source.steps:
            # Only consider steps with ingredient_uses metadata —
            # steps without it are structurally different and can't be safely merged
            if not step.ingredient_uses:
                continue

            for ing_use in step.ingredient_uses:
                # Only merge if we have canonical quantity (successful normalization).
                # Without a common unit, we can't compute a meaningful combined quantity.
                if ing_use.quantity_canonical is None or ing_use.unit_canonical is None:
                    continue

                # The merge key is (ingredient, prep_method) — both must match exactly.
                # "diced onion" and "sliced onion" produce different prep steps, so they don't merge.
                key = (ing_use.ingredient_name, ing_use.prep_method)
                if key not in candidates:
                    candidates[key] = []

                candidates[key].append((
                    step.step_id,
                    recipe_name,
                    ing_use.quantity_canonical,
                    ing_use.unit_canonical,
                ))

    # Filter to only mergeable groups (2+ steps) — singletons can't be merged
    return {k: v for k, v in candidates.items() if len(v) >= 2}


def _create_merged_steps(
    merge_candidates: dict[tuple[str, str], list[tuple[str, str, float, str]]],
    all_steps: list[_StepInfo],
) -> tuple[list[_StepInfo], dict[str, str]]:
    """
    Create synthetic merged prep nodes for detected candidates.

    Returns:
        - Updated step list with merged steps added and original steps removed
        - step_id_rewiring dict: original_step_id → merged_step_id
    """
    step_id_rewiring: dict[str, str] = {}
    steps_to_remove: set[str] = set()
    merged_steps: list[_StepInfo] = []

    merge_counter = 1  # Ensures unique IDs for each merged node

    for (ingredient, prep_method), matches in merge_candidates.items():
        # Construct a deterministic, human-readable merged step ID.
        # Spaces are replaced with underscores for stable DAG node identity.
        merged_step_id = f"merged_{ingredient}_{prep_method}_{merge_counter}".replace(" ", "_").lower()
        merge_counter += 1

        # Sum quantities — the merged prep step handles the combined volume for all recipes
        total_quantity = sum(qty for _, _, qty, _ in matches)
        unit = matches[0][3]  # All should have same unit (same canonical unit)

        # allocation maps each recipe to its share of the merged quantity,
        # so the renderer can print "dice 2 cups for Recipe A, 1 cup for Recipe B"
        allocation: dict[str, str] = {}
        for step_id, recipe_name, qty, unit_str in matches:
            allocation[recipe_name] = f"{qty} {unit_str}"
            # Record the rewrite so downstream edge logic points to the merged node
            step_id_rewiring[step_id] = merged_step_id
            steps_to_remove.add(step_id)

        # Collect all original steps to aggregate metadata
        original_steps = [
            s for s in all_steps
            if s.step_id in {step_id for step_id, _, _, _ in matches}
        ]

        if not original_steps:
            continue

        original_step = original_steps[0]  # For fields where first match is fine

        # Use max duration across all matched steps — merged prep handles
        # the combined quantity, so it takes at least as long as the slowest original.
        # This is conservative but safe: underestimating prep time is worse than overestimating.
        max_duration = max(s.duration_minutes for s in original_steps)
        max_duration_max = max(
            (s.duration_max for s in original_steps if s.duration_max is not None),
            default=None,
        )

        # Synthesise a description summarising the merged work for the renderer/chef
        merged_description = f"Prep {total_quantity} {unit} {prep_method} {ingredient}"

        merged_step = _StepInfo(
            step_id=merged_step_id,
            recipe_name="[merged]",  # Synthetic recipe name — signals this is a cross-recipe step
            recipe_slug="merged",
            description=merged_description,
            resource=original_step.resource,
            duration_minutes=max_duration,
            duration_max=max_duration_max,
            required_equipment=original_step.required_equipment.copy(),
            can_be_done_ahead=original_step.can_be_done_ahead,
            prep_ahead_window=original_step.prep_ahead_window,
            prep_ahead_notes=original_step.prep_ahead_notes,
            # Merged step has no inbound dependencies — it runs ASAP since it feeds multiple recipes.
            # Dependencies will be rebuilt from the rewired edge list in _merge_dags().
            depends_on=[],
        )

        # Attach merge provenance so ScheduledStep can carry merged_from and allocation
        # through to the renderer without losing the original step context.
        merged_step.merged_from = [step_id for step_id, _, _, _ in matches]
        merged_step.allocation = allocation

        merged_steps.append(merged_step)

    # Remove the original steps that were merged and append the new synthetic nodes
    updated_steps = [s for s in all_steps if s.step_id not in steps_to_remove]
    updated_steps.extend(merged_steps)

    return updated_steps, step_id_rewiring


def _rewire_dependencies(
    all_edges: list[tuple[str, str]],
    step_id_rewiring: dict[str, str],
) -> list[tuple[str, str]]:
    """
    Rewire edges to point to merged step IDs where applicable.

    Rules:
    - If source step was merged, outgoing edges become edges from merged step
    - If target step was merged, incoming edges become edges to merged step
    - Deduplicate edges after rewiring
    """
    rewired_edges: set[tuple[str, str]] = set()

    for src, dst in all_edges:
        # Rewire source — if this step was consumed by a merge, its outgoing edges now originate from merged node
        new_src = step_id_rewiring.get(src, src)
        # Rewire destination — if the target was consumed, incoming edges now point to merged node
        new_dst = step_id_rewiring.get(dst, dst)

        # Skip self-loops (can happen if both endpoints were merged to same node)
        # A self-loop would be an invalid DAG edge and would corrupt the topological sort.
        if new_src != new_dst:
            rewired_edges.add((new_src, new_dst))

    return list(rewired_edges)


def _merge_dags(
    recipe_dags: list[RecipeDAG],
    validated_recipes: list[ValidatedRecipe],
    kitchen_config: dict,
    serving_time: str | None = None,
) -> MergedDAG:
    """
    Pure algorithmic greedy list scheduler.

    Joins recipe_dags (edges) with validated_recipes (step details),
    detects shared prep opportunities, merges exact ingredient+prep matches,
    computes critical paths, and schedules steps one at a time in
    priority order at the earliest feasible time.

    If serving_time is set, applies finish-together offsets to cooking
    steps (STOVETOP/OVEN) so all recipes finish together. Prep steps
    (HANDS/PASSIVE) remain ASAP.
    """
    # ── Resource capacity setup ──
    # Burner slots are derived first because stovetop capacity == len(burner_slots).
    # The capacities dict is the single source of truth for all resource pool limits.
    burner_slots = _build_burner_slots(kitchen_config)
    max_burners = len(burner_slots)
    has_second_oven = kitchen_config.get("has_second_oven", False)
    capacities: dict[Resource, float] = {
        Resource.STOVETOP: max_burners,
        Resource.HANDS: 1,  # One pair of hands — can't do two active tasks simultaneously
        Resource.OVEN: 1 * (2 if has_second_oven else 1),
        Resource.PASSIVE: float("inf"),  # Passive steps (resting, marinating) need no cook attention
    }

    # Build lookup: recipe_name → ValidatedRecipe
    # ValidatedRecipe carries the fully enriched step list that we'll join with DAG edges
    vr_by_name = {vr.source.source.name: vr for vr in validated_recipes}

    # Build lookup: recipe_name → course (for entree-anchor oven conflict classification)
    # The course influences how the scheduler classifies oven temperature conflicts —
    # entree conflicts are softened since the entree is the least movable element.
    course_by_recipe: dict[str, Optional[str]] = {
        name: vr.source.source.course
        for name, vr in vr_by_name.items()
    }

    # ── Flatten all per-recipe steps and edges into a single unified lists ──
    all_steps: list[_StepInfo] = []
    all_edges: list[tuple[str, str]] = []

    for dag in recipe_dags:
        vr = vr_by_name.get(dag.recipe_name)
        if vr is None:
            raise ResourceConflictError(f"No validated recipe found for '{dag.recipe_name}'")

        for step in vr.source.steps:
            info = _StepInfo(
                step_id=step.step_id,
                recipe_name=dag.recipe_name,
                recipe_slug=dag.recipe_slug,
                description=step.description,
                resource=step.resource,
                duration_minutes=step.duration_minutes,
                duration_max=step.duration_max,
                required_equipment=list(step.required_equipment),
                can_be_done_ahead=step.can_be_done_ahead,
                prep_ahead_window=step.prep_ahead_window,
                prep_ahead_notes=step.prep_ahead_notes,
                depends_on=list(step.depends_on),
                oven_temp_f=step.oven_temp_f,  # Preserve oven temperature metadata
                # Only extract stovetop heat for STOVETOP steps — avoids false matches in other steps
                stovetop_heat_f=_extract_stovetop_heat_f(step.description) if step.resource == Resource.STOVETOP else None,
            )
            all_steps.append(info)

        all_edges.extend(dag.edges)

    # ── Prep merging: collapse identical ingredient+prep steps across recipes ──
    # This is optional (no merge candidates → no-op). When it fires, synthetic
    # "merged" nodes replace the originals and edges are rewired accordingly.
    merge_candidates = _detect_merge_candidates(validated_recipes)
    if merge_candidates:
        all_steps, step_id_rewiring = _create_merged_steps(merge_candidates, all_steps)
        all_edges = _rewire_dependencies(all_edges, step_id_rewiring)

        # Rebuild depends_on for ALL steps from the authoritative rewired edge list.
        # This ensures: (a) merged steps inherit aggregated inbound dependencies,
        # (b) steps that depended on now-merged originals point to the merged step ID,
        # (c) stale pre-merge step IDs are eliminated.
        deps_from_edges: dict[str, list[str]] = {s.step_id: [] for s in all_steps}
        for src, dst in all_edges:
            if dst in deps_from_edges:
                deps_from_edges[dst].append(src)
        for s in all_steps:
            # sorted+set ensures deterministic, deduplicated dependency lists
            s.depends_on = sorted(set(deps_from_edges.get(s.step_id, [])))
    else:
        step_id_rewiring = {}

    if not all_steps:
        raise ResourceConflictError("No steps to schedule")

    # ── Critical path computation ──
    # Done once up front; the result is stored on each _StepInfo for use as a sort key.
    # This is the "longest path to sink" metric that LPT-style greedy schedulers use
    # to prioritise the steps most likely to become the makespan bottleneck.
    cp = _compute_critical_paths(all_steps, all_edges)
    for s in all_steps:
        s.critical_path_length = cp[s.step_id]

    # ── Finish-together scheduling ──
    # If serving_time is set, compute per-recipe cooking offsets before placement begins.
    # The pre-schedule oven conflict check runs against planned (ideal) windows —
    # if those windows are already irreconcilable, fail fast before any greedy iteration.
    finish_offsets = _compute_finish_together_offsets(all_steps, all_edges, serving_time)
    planned_oven_intervals = _build_planned_oven_intervals(all_steps, all_edges, finish_offsets, course_by_recipe)
    planned_one_oven_conflict = _build_one_oven_conflict_metadata(
        planned_oven_intervals,
        has_second_oven=has_second_oven,
        # treat_overlap_as_irreconcilable=True when finish-together is active:
        # the requested simultaneous oven windows are a hard contract, not a preference.
        treat_overlap_as_irreconcilable=serving_time is not None,
    )
    if planned_one_oven_conflict.classification == "irreconcilable":
        # Fail fast: the requested menu is physically impossible with this kitchen configuration.
        # dag_merger_router will route this ErrorType.RESOURCE_CONFLICT to retry_generation.
        raise ResourceConflictError(
            planned_one_oven_conflict.remediation.notes
            or "Single-oven schedule is irreconcilable for the requested temperature windows.",
            metadata=planned_one_oven_conflict.model_dump(),
        )

    # ── Scheduling state initialisation ──
    step_map = {s.step_id: s for s in all_steps}
    # One _IntervalIndex per resource type for O(log n) overlap queries
    resource_intervals: dict[Resource, _IntervalIndex] = {r: _IntervalIndex() for r in Resource}
    # Per-burner interval tracking — stovetop bypasses the pooled resource_intervals
    burner_intervals: dict[str, _IntervalIndex] = {slot.burner_id: _IntervalIndex() for slot in burner_slots}
    # Burner history stores (end_minute, heat_f) pairs for heat-continuity scoring
    burner_history: dict[str, list[tuple[int, Optional[int]]]] = {slot.burner_id: [] for slot in burner_slots}
    # oven_intervals accumulates scheduled oven windows for post-schedule conflict metadata
    oven_intervals: list[_OvenInterval] = []  # Track oven usage with temperature for conflict detection

    # Equipment intervals — each piece of equipment has capacity 1.
    # Equipment names come from kitchen_config["equipment"] as strings or {"name": ...} dicts.
    equipment_names: set[str] = set()
    for eq in kitchen_config.get("equipment", []):
        if isinstance(eq, str):
            equipment_names.add(eq)
        elif isinstance(eq, dict) and "name" in eq:
            equipment_names.add(eq["name"])
    equipment_intervals: dict[str, _IntervalIndex] = {name: _IntervalIndex() for name in equipment_names}

    scheduled_end: dict[str, int] = {}  # step_id → absolute end minute (dependency unlock signal)
    remaining = set(s.step_id for s in all_steps)
    result: list[ScheduledStep] = []

    # ── Greedy list scheduling loop ──
    # Each iteration picks the highest-priority ready step, finds its earliest feasible
    # start time, records the resource reservation, and locks it in. This is O(n² log n)
    # in the worst case (n steps, each requiring O(n) ready-set scan + O(log n) bisect).
    while remaining:
        # A step is "ready" when all its predecessors have been scheduled (their end times are known)
        ready = [step_map[sid] for sid in remaining if all(dep in scheduled_end for dep in step_map[sid].depends_on)]

        if not ready:
            # Deadlock: remaining steps have unsatisfied deps that will never be satisfied.
            # This should be caught by dag_builder's cycle detection, but we guard here too.
            raise ResourceConflictError(
                f"Deadlock: {len(remaining)} steps remain but none are ready. Remaining: {sorted(remaining)}"
            )

        # Priority: longest critical path first (reduces risk of makespan-stretching delays),
        # with recipe_slug + step_id as deterministic tiebreakers to prevent flaky schedules.
        ready.sort(key=lambda s: (-s.critical_path_length, s.recipe_slug, s.step_id))
        step = ready[0]

        # Earliest start from dependency constraints — max of all predecessor end times
        earliest = max(
            (scheduled_end[dep] for dep in step.depends_on),
            default=0,
        )

        # Apply finish-together offset for cooking steps only (STOVETOP/OVEN)
        # Prep steps (HANDS/PASSIVE) remain ASAP — they don't affect the "when does plating happen"
        if _is_cooking_step(step.resource) and step.recipe_name in finish_offsets:
            earliest = max(earliest, finish_offsets[step.recipe_name])

        # ── Resource placement ──
        # STOVETOP uses the burner-slot model (S02); all others use the pooled interval index.
        assigned_burner: Optional[_BurnerSlot] = None
        if step.resource == Resource.STOVETOP:
            # S02: explicit burner slots are the authoritative stovetop scheduling model.
            # Do not reserve pooled STOVETOP capacity here; burner-local occupancy below is
            # the source of truth for feasibility, release-boundary waiting, and assignment.
            start, assigned_burner = _find_stovetop_slot(
                step,
                earliest,
                burner_slots,
                burner_intervals,
                burner_history,
            )
        else:
            start = _find_earliest_start(
                step.resource,
                step.duration_minutes,
                earliest,
                resource_intervals,
                capacities,
                # Pass oven_intervals and temp only for OVEN steps — avoids irrelevant processing
                oven_intervals=oven_intervals if step.resource == Resource.OVEN else None,
                oven_temp_f=step.oven_temp_f if step.resource == Resource.OVEN else None,
                step_recipe_name=step.recipe_name if step.resource == Resource.OVEN else None,
            )

        # ── Equipment conflict resolution ──
        # Equipment constraints are applied after resource placement because they
        # are secondary constraints (a burner or oven slot must be free first).
        # Iterating up to 10k times is the safety valve for pathological equipment graphs.
        constrained_equipment = [eq for eq in step.required_equipment if eq in equipment_intervals]
        if constrained_equipment:
            for _ in range(10_000):  # safety valve
                end_candidate = start + step.duration_minutes
                conflict = False
                for eq in constrained_equipment:
                    if equipment_intervals[eq].count_overlapping(start, end_candidate) >= 1:
                        # Equipment is in use — advance past its current occupancy
                        next_end = equipment_intervals[eq].min_end_after(start)
                        if next_end is not None:
                            start = next_end
                        conflict = True
                        break
                if not conflict:
                    # Equipment is now free — but advancing start may have broken the
                    # resource constraint, so re-check resource availability at the new time.
                    if step.resource == Resource.STOVETOP:
                        start, assigned_burner = _find_stovetop_slot(
                            step,
                            start,
                            burner_slots,
                            burner_intervals,
                            burner_history,
                        )
                    else:
                        start = _find_earliest_start(
                            step.resource,
                            step.duration_minutes,
                            start,
                            resource_intervals,
                            capacities,
                            oven_intervals=oven_intervals if step.resource == Resource.OVEN else None,
                            oven_temp_f=step.oven_temp_f if step.resource == Resource.OVEN else None,
                            step_recipe_name=step.recipe_name if step.resource == Resource.OVEN else None,
                        )
                    break

        end = start + step.duration_minutes

        # ── Commit the resource reservation ──
        # PASSIVE steps don't consume any tracked capacity — they run "for free"
        if step.resource == Resource.STOVETOP:
            if assigned_burner is None:
                raise ResourceConflictError(f"Cannot schedule stovetop step {step.step_id}: burner assignment missing")
            burner_intervals[assigned_burner.burner_id].add(start, end)
            # Record heat history so later steps on this burner can preference heat continuity
            burner_history.setdefault(assigned_burner.burner_id, []).append((end, step.stovetop_heat_f))
        elif step.resource != Resource.PASSIVE:
            # HANDS and OVEN use the pooled resource_intervals
            resource_intervals[step.resource].add(start, end)

        # Separately track oven intervals with temperature for post-schedule conflict metadata.
        # This is distinct from the resource_intervals tracking — it carries temp info.
        if step.resource == Resource.OVEN:
            oven_intervals.append(_OvenInterval(
                start=start,
                end=end,
                temp_f=step.oven_temp_f,
                recipe_name=step.recipe_name,
                step_id=step.step_id,
                course=course_by_recipe.get(step.recipe_name),
            ))

        # Record equipment intervals
        for eq in constrained_equipment:
            equipment_intervals[eq].add(start, end)

        # Mark this step as scheduled — unlocks any steps that depend on it
        scheduled_end[step.step_id] = end
        remaining.remove(step.step_id)

        result.append(
            ScheduledStep(
                step_id=step.step_id,
                recipe_name=step.recipe_name,
                description=step.description,
                resource=step.resource,
                duration_minutes=step.duration_minutes,
                duration_max=step.duration_max,
                start_at_minute=start,
                end_at_minute=end,
                required_equipment=step.required_equipment,
                can_be_done_ahead=step.can_be_done_ahead,
                prep_ahead_window=step.prep_ahead_window,
                prep_ahead_notes=step.prep_ahead_notes,
                depends_on=step.depends_on,
                merged_from=step.merged_from,
                allocation=step.allocation,
                oven_temp_f=step.oven_temp_f,
                # Burner fields are None for non-STOVETOP steps
                burner_id=assigned_burner.burner_id if assigned_burner else None,
                burner_position=assigned_burner.position if assigned_burner else None,
                burner_size=assigned_burner.size if assigned_burner else None,
                burner_label=assigned_burner.label if assigned_burner else None,
                burner=assigned_burner.to_descriptor() if assigned_burner else None,
            )
        )

    # Sort output deterministically: time-primary, recipe secondary, step_id tertiary.
    # This makes the timeline view stable and unit-testable without relying on insertion order.
    result.sort(key=lambda s: (s.start_at_minute, s.recipe_name, s.step_id))

    # ── Summary statistics ──
    # total = makespan (wall-clock time from start to last step finishing)
    total = max(s.end_at_minute for s in result)
    # active = sum of all non-passive step durations — measures actual cook workload
    active = sum(s.duration_minutes for s in result if s.resource != Resource.PASSIVE)

    # ── Worst-case pass: compute end_at_minute_max, slack, and total_max ──
    # Build successor map from edges for slack computation
    successors: dict[str, list[str]] = {s.step_id: [] for s in result}
    for src, dst in all_edges:
        if src in successors:
            successors[src].append(dst)

    result_by_id = {s.step_id: s for s in result}

    # Worst-case end: start + duration_max (pessimistic bound for planning buffers)
    for s in result:
        dur_max = s.duration_max if s.duration_max else s.duration_minutes
        s.end_at_minute_max = s.start_at_minute + dur_max

    # Slack: how many minutes a step can overrun before it delays any successor.
    # slack = earliest_successor_start - this_step_worst_case_end
    # Terminal steps (no successors) have slack 0 — they're already at the end of the chain.
    for s in result:
        succ_starts = [result_by_id[sid].start_at_minute for sid in successors[s.step_id] if sid in result_by_id]
        if succ_starts:
            s.slack_minutes = max(0, min(succ_starts) - (s.end_at_minute_max or s.end_at_minute))
        else:
            # No successors — slack is unbounded, set 0 (terminal step)
            s.slack_minutes = 0

    # total_max = worst-case makespan if all steps hit their duration_max upper bounds
    total_max = max(s.end_at_minute_max for s in result if s.end_at_minute_max is not None)

    # ── Resource utilisation map ──
    # Converts _IntervalIndex contents to sorted (start, end) lists for the output model.
    # Empty resources are omitted — no point surfacing "OVEN: []" if the menu has no oven steps.
    utilisation: dict[str, list[tuple[int, int]]] = {}
    for resource, index in resource_intervals.items():
        if len(index) > 0:
            utilisation[resource.value] = index.intervals()

    # Equipment utilisation follows the same pattern
    eq_utilisation: dict[str, list[tuple[int, int]]] = {}
    for eq_name, index in equipment_intervals.items():
        if len(index) > 0:
            eq_utilisation[eq_name] = index.intervals()

    # ── Post-schedule conflict metadata ──
    # Detect finish-together failures (resource constraints frustrated the intent)
    resource_warnings = _detect_resource_warnings(result, finish_offsets, capacities)
    # Use the pre-schedule (planned) oven conflict if it was already classified —
    # fallback to re-running against actual scheduled windows if it was "compatible".
    # This ensures we don't lose a resequence_required classification just because
    # the scheduler happened to avoid overlap through resource deferral.
    one_oven_conflict = planned_one_oven_conflict
    if one_oven_conflict.classification == "compatible":
        # Re-examine actual scheduled windows — the scheduler may have introduced
        # resequencing implicitly by pushing one step behind another.
        one_oven_conflict = _build_one_oven_conflict_metadata(oven_intervals, has_second_oven=has_second_oven)
    elif one_oven_conflict.classification == "resequence_required" and one_oven_conflict.remediation.notes is None:
        # Planned classification exists but lacks notes — re-run with actual windows for full detail
        one_oven_conflict = _build_one_oven_conflict_metadata(oven_intervals, has_second_oven=has_second_oven)

    return MergedDAG(
        scheduled_steps=result,
        total_duration_minutes=total,
        # Only surface total_max if it differs from total (i.e. some step has a duration_max)
        total_duration_minutes_max=total_max if total_max != total else None,
        active_time_minutes=active,
        resource_utilisation=utilisation,
        equipment_utilisation=eq_utilisation,
        resource_warnings=resource_warnings,
        one_oven_conflict=one_oven_conflict,
    )


async def dag_merger_node(state: GRASPState) -> dict:
    """LangGraph node: merges per-recipe DAGs into a single schedule."""
    dag_dicts = state.get("recipe_dags", [])
    validated_dicts = state.get("validated_recipes", [])
    kitchen_config = state.get("kitchen_config", {})

    # Extract serving_time for finish-together scheduling
    # serving_time lives on the DinnerConcept, not top-level state
    concept = state.get("concept", {})
    serving_time = concept.get("serving_time") if concept else None

    try:
        # Re-validate from dicts here because GRASPState carries everything as dicts
        # (replace semantics requirement — operator.add is only for errors).
        recipe_dags = [RecipeDAG.model_validate(d) for d in dag_dicts]
        validated_recipes = [ValidatedRecipe.model_validate(d) for d in validated_dicts]

        merged = _merge_dags(recipe_dags, validated_recipes, kitchen_config, serving_time=serving_time)
        logger.info(
            "Merged %d recipes → %d steps, %d min total",
            len(recipe_dags),
            len(merged.scheduled_steps),
            merged.total_duration_minutes,
        )
        # Return replace semantics: merged_dag is a single dict, not a list.
        # The LangGraph state reducer for merged_dag uses replace (not operator.add).
        return {"merged_dag": merged.model_dump()}

    except ResourceConflictError as exc:
        logger.error("Resource conflict: %s", exc)
        # Normalise metadata: always include "detail" so downstream error handlers
        # have a human-readable message regardless of conflict classification type.
        metadata = dict(exc.metadata) if exc.metadata else {"detail": str(exc)}
        if metadata and "detail" not in metadata:
            metadata["detail"] = str(exc)
        try:
            # If metadata matches OneOvenConflictSummary schema, validate it to ensure
            # a well-typed structure reaches the LangGraph router.
            if "classification" in metadata:
                metadata = OneOvenConflictSummary.model_validate(metadata).model_dump()
                metadata["detail"] = str(exc)
        except ValidationError:
            # Malformed conflict metadata: fall back to just the error message.
            # Better to lose structure than to crash the error path itself.
            logger.warning("Resource conflict metadata failed validation; falling back to detail only")
            metadata = {"detail": str(exc)}
        return {
            "errors": [
                {
                    "node_name": "dag_merger",
                    # RESOURCE_CONFLICT → dag_merger_router sends this to retry_generation
                    "error_type": ErrorType.RESOURCE_CONFLICT.value,
                    # recoverable=False: this error requires a new menu generation pass,
                    # not just a retry of the same input through the same pipeline.
                    "recoverable": False,
                    "message": str(exc),
                    "metadata": metadata,
                }
            ]
        }
    except Exception as exc:
        # Catch-all: unexpected errors (Pydantic validation, NetworkX, etc.)
        # are surfaced as RESOURCE_CONFLICT so the router still triggers retry.
        logger.error("Merge failed: %s: %s", type(exc).__name__, exc)
        return {
            "errors": [
                {
                    "node_name": "dag_merger",
                    "error_type": ErrorType.RESOURCE_CONFLICT.value,
                    "recoverable": False,
                    "message": f"Merge failed: {type(exc).__name__}: {exc}",
                    "metadata": {"exception_type": type(exc).__name__},
                }
            ]
        }
