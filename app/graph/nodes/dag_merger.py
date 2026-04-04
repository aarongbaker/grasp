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
from typing import Optional

import networkx as nx

from app.models.enums import ErrorType, Resource
from app.models.pipeline import GRASPState
from app.models.recipe import ValidatedRecipe
from app.models.scheduling import MergedDAG, RecipeDAG, ScheduledStep

logger = logging.getLogger(__name__)


class ResourceConflictError(Exception):
    """Raised when the scheduler cannot find a valid time slot."""

    pass


@dataclass
class _StepInfo:
    """Internal representation joining DAG edges with step details."""

    step_id: str
    recipe_name: str
    recipe_slug: str
    description: str
    resource: Resource
    duration_minutes: int
    duration_max: Optional[int] = None
    required_equipment: list[str] = field(default_factory=list)
    can_be_done_ahead: bool = False
    prep_ahead_window: Optional[str] = None
    prep_ahead_notes: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)
    critical_path_length: int = 0
    merged_from: list[str] = field(default_factory=list)  # step_ids consolidated into this merged node
    allocation: dict[str, str] = field(default_factory=dict)  # recipe_name → quantity breakdown


def _compute_critical_paths(
    steps: list[_StepInfo],
    all_edges: list[tuple[str, str]],
) -> dict[str, int]:
    """
    Bottom-up critical path: duration of longest path from each step to any
    sink node within its recipe. Used for scheduling priority.
    """
    dur = {s.step_id: s.duration_minutes for s in steps}

    G = nx.DiGraph()
    G.add_nodes_from(dur.keys())
    G.add_edges_from(all_edges)

    cp: dict[str, int] = {}
    for step_id in reversed(list(nx.topological_sort(G))):
        successors = list(G.successors(step_id))
        if not successors:
            cp[step_id] = dur[step_id]
        else:
            cp[step_id] = dur[step_id] + max(cp[s] for s in successors)

    # Handle isolated nodes (no edges) — shouldn't happen but be safe
    for s in steps:
        if s.step_id not in cp:
            cp[s.step_id] = s.duration_minutes

    return cp


def _is_cooking_step(resource: Resource) -> bool:
    """Return True for active cooking resources (STOVETOP, OVEN)."""
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

    # Build edge lookup for filtering per-recipe edges
    step_to_recipe = {s.step_id: s.recipe_name for s in all_steps}

    # For each recipe, compute the critical path length considering only cooking steps
    result: dict[str, int] = {}

    for recipe_name, steps in steps_by_recipe.items():
        # Filter edges to this recipe
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

        # Bottom-up critical path: sum of cooking durations along longest path
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

    # Find the anchor (longest cooking) recipe
    max_cooking = max(cooking_durations.values())

    # Compute offsets: anchor gets 0, shorter recipes get positive offsets
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

    # Check each non-anchor recipe for significant delays
    warnings: list[str] = []
    delay_threshold = 20  # minutes

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

            # Determine suggestion based on resource type
            if resource == Resource.OVEN:
                if capacity == 1:
                    suggestion = f"Consider starting {recipe_name} earlier if you have a second oven."
                else:
                    suggestion = f"You may need additional oven capacity for simultaneous cooking."
            elif resource == Resource.STOVETOP:
                suggestion = f"Consider timing {recipe_name} to start earlier or adding burners."
            else:
                suggestion = f"Consider adjusting timing for {recipe_name}."

            # Find anchor recipe name for message (use first anchor for simplicity)
            anchor_name = anchor_recipes[0]

            warnings.append(
                f"{recipe_name}'s {resource_name} cooking will finish ~{delay_rounded} minutes "
                f"after {anchor_name} due to {resource_name} capacity. {suggestion}"
            )

    return warnings


class _IntervalIndex:
    """Sorted interval index with O(log n) overlap counting."""

    def __init__(self) -> None:
        self._starts: list[int] = []
        self._ends: list[int] = []

    def add(self, start: int, end: int) -> None:
        bisect.insort(self._starts, start)
        bisect.insort(self._ends, end)

    def count_overlapping(self, window_start: int, window_end: int) -> int:
        """Count intervals overlapping [window_start, window_end)."""
        started_before_end = bisect.bisect_left(self._starts, window_end)
        ended_before_start = bisect.bisect_right(self._ends, window_start)
        return started_before_end - ended_before_start

    def min_end_after(self, t: int) -> int | None:
        """Return the smallest end value > t, or None."""
        idx = bisect.bisect_right(self._ends, t)
        return self._ends[idx] if idx < len(self._ends) else None

    def intervals(self) -> list[tuple[int, int]]:
        """Return sorted (start, end) pairs for utilisation output."""
        pairs = list(zip(self._starts, self._ends))
        pairs.sort()
        return pairs

    def __len__(self) -> int:
        return len(self._starts)


def _find_earliest_start(
    resource: Resource,
    duration: int,
    earliest_from_deps: int,
    resource_intervals: dict[Resource, "_IntervalIndex"],
    capacities: dict[Resource, float],
) -> int:
    """
    Find the earliest start time for a step that satisfies resource constraints.
    PASSIVE steps always start at earliest_from_deps (no capacity limit).
    """
    if resource == Resource.PASSIVE:
        return earliest_from_deps

    index = resource_intervals[resource]
    cap = capacities[resource]
    candidate = earliest_from_deps

    for _ in range(10_000):  # safety valve
        window_end = candidate + duration
        overlap_count = index.count_overlapping(candidate, window_end)

        if overlap_count < cap:
            return candidate

        # Advance past the earliest-ending overlapping interval after candidate
        next_end = index.min_end_after(candidate)
        if next_end is None:
            return candidate  # shouldn't happen, but be safe
        candidate = next_end

    raise ResourceConflictError(f"Cannot schedule step: resource {resource.value} exhausted after 10,000 iterations")


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
            # Only consider steps with ingredient_uses metadata
            if not step.ingredient_uses:
                continue

            for ing_use in step.ingredient_uses:
                # Only merge if we have canonical quantity (successful normalization)
                if ing_use.quantity_canonical is None or ing_use.unit_canonical is None:
                    continue

                key = (ing_use.ingredient_name, ing_use.prep_method)
                if key not in candidates:
                    candidates[key] = []

                candidates[key].append((
                    step.step_id,
                    recipe_name,
                    ing_use.quantity_canonical,
                    ing_use.unit_canonical,
                ))

    # Filter to only mergeable groups (2+ steps)
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

    merge_counter = 1

    for (ingredient, prep_method), matches in merge_candidates.items():
        # Create synthetic merged step
        merged_step_id = f"merged_{ingredient}_{prep_method}_{merge_counter}".replace(" ", "_").lower()
        merge_counter += 1

        # Aggregate total quantity
        total_quantity = sum(qty for _, _, qty, _ in matches)
        unit = matches[0][3]  # All should have same unit (same canonical unit)

        # Build allocation dict: recipe_name → quantity string
        allocation: dict[str, str] = {}
        for step_id, recipe_name, qty, unit_str in matches:
            allocation[recipe_name] = f"{qty} {unit_str}"
            step_id_rewiring[step_id] = merged_step_id
            steps_to_remove.add(step_id)

        # Find one of the original steps to copy metadata from (use first match)
        original_step_id = matches[0][0]
        original_step = next((s for s in all_steps if s.step_id == original_step_id), None)

        if original_step is None:
            continue

        # Create merged step with aggregated description
        merged_description = f"Prep {total_quantity} {unit} {prep_method} {ingredient}"

        merged_step = _StepInfo(
            step_id=merged_step_id,
            recipe_name="[merged]",  # Synthetic recipe name
            recipe_slug="merged",
            description=merged_description,
            resource=original_step.resource,
            duration_minutes=original_step.duration_minutes,
            duration_max=original_step.duration_max,
            required_equipment=original_step.required_equipment.copy(),
            can_be_done_ahead=original_step.can_be_done_ahead,
            prep_ahead_window=original_step.prep_ahead_window,
            prep_ahead_notes=original_step.prep_ahead_notes,
            depends_on=[],  # Merged step has no dependencies (earliest prep)
        )

        # Store merge metadata in the step for later use
        # We'll attach this as extra attributes for the scheduler to preserve
        merged_step.merged_from = [step_id for step_id, _, _, _ in matches]
        merged_step.allocation = allocation

        merged_steps.append(merged_step)

    # Remove original steps and add merged steps
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
        # Rewire source
        new_src = step_id_rewiring.get(src, src)
        # Rewire destination
        new_dst = step_id_rewiring.get(dst, dst)

        # Skip self-loops (can happen if both endpoints were merged to same node)
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
    # Resource capacities
    max_burners = kitchen_config.get("max_burners", 4)
    has_second_oven = kitchen_config.get("has_second_oven", False)
    capacities: dict[Resource, float] = {
        Resource.STOVETOP: max_burners,
        Resource.HANDS: 1,
        Resource.OVEN: 1 * (2 if has_second_oven else 1),
        Resource.PASSIVE: float("inf"),
    }

    # Build lookup: recipe_name → ValidatedRecipe
    vr_by_name = {vr.source.source.name: vr for vr in validated_recipes}

    # Build unified step list
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
            )
            all_steps.append(info)

        all_edges.extend(dag.edges)

    # Detect merge candidates and create merged steps
    merge_candidates = _detect_merge_candidates(validated_recipes)
    if merge_candidates:
        all_steps, step_id_rewiring = _create_merged_steps(merge_candidates, all_steps)
        all_edges = _rewire_dependencies(all_edges, step_id_rewiring)
    else:
        step_id_rewiring = {}

    if not all_steps:
        raise ResourceConflictError("No steps to schedule")

    # Compute critical paths
    cp = _compute_critical_paths(all_steps, all_edges)
    for s in all_steps:
        s.critical_path_length = cp[s.step_id]

    # Compute finish-together offsets (empty dict if serving_time is None)
    finish_offsets = _compute_finish_together_offsets(all_steps, all_edges, serving_time)

    # Scheduling state
    step_map = {s.step_id: s for s in all_steps}
    resource_intervals: dict[Resource, _IntervalIndex] = {r: _IntervalIndex() for r in Resource}

    # Equipment intervals — each piece of equipment has capacity 1
    equipment_names: set[str] = set()
    for eq in kitchen_config.get("equipment", []):
        if isinstance(eq, str):
            equipment_names.add(eq)
        elif isinstance(eq, dict) and "name" in eq:
            equipment_names.add(eq["name"])
    equipment_intervals: dict[str, _IntervalIndex] = {name: _IntervalIndex() for name in equipment_names}

    scheduled_end: dict[str, int] = {}
    remaining = set(s.step_id for s in all_steps)
    result: list[ScheduledStep] = []

    while remaining:
        # Find ready steps: all dependencies satisfied
        ready = [step_map[sid] for sid in remaining if all(dep in scheduled_end for dep in step_map[sid].depends_on)]

        if not ready:
            raise ResourceConflictError(
                f"Deadlock: {len(remaining)} steps remain but none are ready. Remaining: {sorted(remaining)}"
            )

        # Sort by priority: critical path desc, slug asc, step_id asc
        ready.sort(key=lambda s: (-s.critical_path_length, s.recipe_slug, s.step_id))
        step = ready[0]

        # Earliest start from dependencies
        earliest = max(
            (scheduled_end[dep] for dep in step.depends_on),
            default=0,
        )

        # Apply finish-together offset for cooking steps only (STOVETOP/OVEN)
        # Prep steps (HANDS/PASSIVE) remain ASAP
        if _is_cooking_step(step.resource) and step.recipe_name in finish_offsets:
            earliest = max(earliest, finish_offsets[step.recipe_name])

        # Find earliest feasible start respecting resource constraints
        start = _find_earliest_start(
            step.resource,
            step.duration_minutes,
            earliest,
            resource_intervals,
            capacities,
        )

        # Advance past equipment conflicts (each equipment piece has capacity 1)
        constrained_equipment = [eq for eq in step.required_equipment if eq in equipment_intervals]
        if constrained_equipment:
            for _ in range(10_000):  # safety valve
                end_candidate = start + step.duration_minutes
                conflict = False
                for eq in constrained_equipment:
                    if equipment_intervals[eq].count_overlapping(start, end_candidate) >= 1:
                        next_end = equipment_intervals[eq].min_end_after(start)
                        if next_end is not None:
                            start = next_end
                        conflict = True
                        break
                if not conflict:
                    # Also re-check resource constraint at new start (equipment may have pushed us)
                    start = _find_earliest_start(
                        step.resource,
                        step.duration_minutes,
                        start,
                        resource_intervals,
                        capacities,
                    )
                    break

        end = start + step.duration_minutes

        # Record resource interval (PASSIVE doesn't consume capacity)
        if step.resource != Resource.PASSIVE:
            resource_intervals[step.resource].add(start, end)

        # Record equipment intervals
        for eq in constrained_equipment:
            equipment_intervals[eq].add(start, end)

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
            )
        )

    # Sort output deterministically
    result.sort(key=lambda s: (s.start_at_minute, s.recipe_name, s.step_id))

    total = max(s.end_at_minute for s in result)
    active = sum(s.duration_minutes for s in result if s.resource != Resource.PASSIVE)

    # ── Worst-case pass: compute end_at_minute_max, slack, and total_max ──
    # Build successor map from edges
    successors: dict[str, list[str]] = {s.step_id: [] for s in result}
    for src, dst in all_edges:
        if src in successors:
            successors[src].append(dst)

    result_by_id = {s.step_id: s for s in result}

    # Compute worst-case end for each step
    for s in result:
        dur_max = s.duration_max if s.duration_max else s.duration_minutes
        s.end_at_minute_max = s.start_at_minute + dur_max

    # Compute slack: how much a step can overrun before delaying any successor
    for s in result:
        succ_starts = [result_by_id[sid].start_at_minute for sid in successors[s.step_id] if sid in result_by_id]
        if succ_starts:
            s.slack_minutes = max(0, min(succ_starts) - (s.end_at_minute_max or s.end_at_minute))
        else:
            # No successors — slack is unbounded, set 0 (terminal step)
            s.slack_minutes = 0

    total_max = max(s.end_at_minute_max for s in result if s.end_at_minute_max is not None)

    # Build resource utilisation (sorted intervals, skip empty)
    utilisation: dict[str, list[tuple[int, int]]] = {}
    for resource, index in resource_intervals.items():
        if len(index) > 0:
            utilisation[resource.value] = index.intervals()

    # Build equipment utilisation
    eq_utilisation: dict[str, list[tuple[int, int]]] = {}
    for eq_name, index in equipment_intervals.items():
        if len(index) > 0:
            eq_utilisation[eq_name] = index.intervals()

    # Detect resource constraint warnings for finish-together scheduling
    resource_warnings = _detect_resource_warnings(result, finish_offsets, capacities)

    return MergedDAG(
        scheduled_steps=result,
        total_duration_minutes=total,
        total_duration_minutes_max=total_max if total_max != total else None,
        active_time_minutes=active,
        resource_utilisation=utilisation,
        equipment_utilisation=eq_utilisation,
        resource_warnings=resource_warnings,
    )


async def dag_merger_node(state: GRASPState) -> dict:
    """LangGraph node: merges per-recipe DAGs into a single schedule."""
    dag_dicts = state.get("recipe_dags", [])
    validated_dicts = state.get("validated_recipes", [])
    kitchen_config = state.get("kitchen_config", {})
    
    # Extract serving_time for finish-together scheduling
    concept = state.get("concept", {})
    serving_time = concept.get("serving_time") if concept else None

    try:
        recipe_dags = [RecipeDAG.model_validate(d) for d in dag_dicts]
        validated_recipes = [ValidatedRecipe.model_validate(d) for d in validated_dicts]

        merged = _merge_dags(recipe_dags, validated_recipes, kitchen_config, serving_time=serving_time)
        logger.info(
            "Merged %d recipes → %d steps, %d min total",
            len(recipe_dags),
            len(merged.scheduled_steps),
            merged.total_duration_minutes,
        )
        return {"merged_dag": merged.model_dump()}

    except ResourceConflictError as exc:
        logger.error("Resource conflict: %s", exc)
        return {
            "errors": [
                {
                    "node_name": "dag_merger",
                    "error_type": ErrorType.RESOURCE_CONFLICT.value,
                    "recoverable": False,
                    "message": str(exc),
                    "metadata": {"detail": str(exc)},
                }
            ]
        }
    except Exception as exc:
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
