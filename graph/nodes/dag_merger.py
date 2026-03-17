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

import logging
from dataclasses import dataclass, field
from typing import Optional

import networkx as nx

from models.enums import ErrorType, Resource
from models.pipeline import GRASPState
from models.recipe import ValidatedRecipe
from models.scheduling import MergedDAG, RecipeDAG, ScheduledStep

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
    can_be_done_ahead: bool = False
    prep_ahead_window: Optional[str] = None
    prep_ahead_notes: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)
    critical_path_length: int = 0


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


def _count_overlapping(
    intervals: list[tuple[int, int]],
    window_start: int,
    window_end: int,
) -> int:
    """Count how many intervals overlap with [window_start, window_end)."""
    return sum(1 for (a, b) in intervals if a < window_end and b > window_start)


def _find_earliest_start(
    resource: Resource,
    duration: int,
    earliest_from_deps: int,
    resource_intervals: dict[Resource, list[tuple[int, int]]],
    capacities: dict[Resource, float],
) -> int:
    """
    Find the earliest start time for a step that satisfies resource constraints.
    PASSIVE steps always start at earliest_from_deps (no capacity limit).
    """
    if resource == Resource.PASSIVE:
        return earliest_from_deps

    intervals = resource_intervals[resource]
    cap = capacities[resource]
    candidate = earliest_from_deps

    for _ in range(10_000):  # safety valve
        window_end = candidate + duration
        overlap_count = _count_overlapping(intervals, candidate, window_end)

        if overlap_count < cap:
            return candidate

        # Advance past the earliest-ending overlapping interval
        overlapping_ends = [b for (a, b) in intervals if a < window_end and b > candidate]
        if not overlapping_ends:
            return candidate  # shouldn't happen, but be safe
        candidate = min(overlapping_ends)

    raise ResourceConflictError(f"Cannot schedule step: resource {resource.value} exhausted after 10,000 iterations")


def _merge_dags(
    recipe_dags: list[RecipeDAG],
    validated_recipes: list[ValidatedRecipe],
    kitchen_config: dict,
) -> MergedDAG:
    """
    Pure algorithmic greedy list scheduler.

    Joins recipe_dags (edges) with validated_recipes (step details),
    computes critical paths, and schedules steps one at a time in
    priority order at the earliest feasible time.
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
                can_be_done_ahead=step.can_be_done_ahead,
                prep_ahead_window=step.prep_ahead_window,
                prep_ahead_notes=step.prep_ahead_notes,
                depends_on=list(step.depends_on),
            )
            all_steps.append(info)

        all_edges.extend(dag.edges)

    if not all_steps:
        raise ResourceConflictError("No steps to schedule")

    # Compute critical paths
    cp = _compute_critical_paths(all_steps, all_edges)
    for s in all_steps:
        s.critical_path_length = cp[s.step_id]

    # Scheduling state
    step_map = {s.step_id: s for s in all_steps}
    resource_intervals: dict[Resource, list[tuple[int, int]]] = {r: [] for r in Resource}
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

        # Find earliest feasible start respecting resource constraints
        start = _find_earliest_start(
            step.resource,
            step.duration_minutes,
            earliest,
            resource_intervals,
            capacities,
        )
        end = start + step.duration_minutes

        # Record resource interval (PASSIVE doesn't consume capacity)
        if step.resource != Resource.PASSIVE:
            resource_intervals[step.resource].append((start, end))

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
                can_be_done_ahead=step.can_be_done_ahead,
                prep_ahead_window=step.prep_ahead_window,
                prep_ahead_notes=step.prep_ahead_notes,
                depends_on=step.depends_on,
            )
        )

    # Sort output deterministically
    result.sort(key=lambda s: (s.start_at_minute, s.recipe_name, s.step_id))

    total = max(s.end_at_minute for s in result)

    # Build resource utilisation (sorted intervals, skip empty)
    utilisation: dict[str, list[tuple[int, int]]] = {}
    for resource, intervals in resource_intervals.items():
        if intervals:
            utilisation[resource.value] = sorted(intervals)

    return MergedDAG(
        scheduled_steps=result,
        total_duration_minutes=total,
        resource_utilisation=utilisation,
    )


async def dag_merger_node(state: GRASPState) -> dict:
    """LangGraph node: merges per-recipe DAGs into a single schedule."""
    dag_dicts = state.get("recipe_dags", [])
    validated_dicts = state.get("validated_recipes", [])
    kitchen_config = state.get("kitchen_config", {})

    try:
        recipe_dags = [RecipeDAG.model_validate(d) for d in dag_dicts]
        validated_recipes = [ValidatedRecipe.model_validate(d) for d in validated_dicts]

        merged = _merge_dags(recipe_dags, validated_recipes, kitchen_config)
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
