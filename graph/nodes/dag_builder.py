"""
graph/nodes/dag_builder.py
Real DAG builder — Phase 6. Pure algorithmic, no LLM call.

Reads validated_recipes from GRASPState, extracts step dependencies,
builds a NetworkX DiGraph per recipe, validates acyclicity, and returns
RecipeDAG models with edge lists.

Error handling: per-recipe recoverable. If one recipe has a cycle or
invalid dependencies, it is dropped and the pipeline continues with
survivors. If ALL recipes fail, the error is fatal (recoverable=False).

IDEMPOTENCY: Returns recipe_dags as a NEW list (not appended).
Replace semantics — same contract as generator (§2.10).

Mockable seam:
  _build_single_dag()  — builds one RecipeDAG from a ValidatedRecipe
Tests can patch this function to inject failures.

SIMULATE_INTERRUPT:
  Preserved from mock_dag_builder for test_run4 (checkpoint resume).
  When os.environ.get("SIMULATE_INTERRUPT") == "1", raises RuntimeError
  before completing. LangGraph resumes from validator checkpoint.
"""

import logging
import os
import re

import networkx as nx

from models.enums import ErrorType
from models.pipeline import GRASPState
from models.recipe import ValidatedRecipe
from models.scheduling import RecipeDAG

logger = logging.getLogger(__name__)


def _generate_recipe_slug(name: str) -> str:
    """Convert recipe name to a URL-safe slug. 'Braised Short Ribs' → 'braised_short_ribs'"""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _build_single_dag(validated: ValidatedRecipe) -> RecipeDAG:
    """
    Build a RecipeDAG from a ValidatedRecipe.

    Extracts step IDs and dependency edges from the EnrichedRecipe's steps,
    constructs a NetworkX DiGraph, and validates acyclicity.

    Raises ValueError on cycle detection or dangling dependency references.
    """
    enriched = validated.source
    raw = enriched.source
    slug = _generate_recipe_slug(raw.name)
    steps = enriched.steps
    step_ids = {s.step_id for s in steps}

    # Build edges from depends_on fields
    edges: list[tuple[str, str]] = []
    for step in steps:
        for dep_id in step.depends_on:
            if dep_id not in step_ids:
                raise ValueError(
                    f"Dangling dependency in '{raw.name}': "
                    f"step '{step.step_id}' depends on '{dep_id}' "
                    f"which does not exist. Available: {sorted(step_ids)}"
                )
            edges.append((dep_id, step.step_id))

    # Validate with NetworkX
    G = nx.DiGraph()
    G.add_nodes_from(step_ids)
    G.add_edges_from(edges)

    if not nx.is_directed_acyclic_graph(G):
        cycles = list(nx.simple_cycles(G))
        raise ValueError(f"Cycle(s) detected in '{raw.name}': {cycles}")

    return RecipeDAG(
        recipe_name=raw.name,
        recipe_slug=slug,
        steps=[],       # Steps live in EnrichedRecipe; DAG stores edges only
        edges=edges,
    )


async def dag_builder_node(state: GRASPState) -> dict:
    """LangGraph node: builds per-recipe dependency DAGs."""

    # Checkpoint resume test: simulate crash before completing
    if os.environ.get("SIMULATE_INTERRUPT") == "1":
        raise RuntimeError(
            "SIMULATE_INTERRUPT: dag_builder crashed. "
            "LangGraph will resume from validator checkpoint on next invoke."
        )

    validated_dicts = state.get("validated_recipes", [])

    dags: list[dict] = []
    errors: list[dict] = []

    for recipe_dict in validated_dicts:
        recipe_name = (
            recipe_dict.get("source", {}).get("source", {}).get("name", "unknown")
        )
        try:
            validated = ValidatedRecipe.model_validate(recipe_dict)
            dag = _build_single_dag(validated)
            dags.append(dag.model_dump())
            logger.info("DAG built for '%s': %d edges", recipe_name, len(dag.edges))
        except Exception as exc:
            logger.warning("DAG build failed for '%s': %s", recipe_name, exc)
            errors.append({
                "node_name": "dag_builder",
                "error_type": ErrorType.DEPENDENCY_RESOLUTION.value,
                "recoverable": True,
                "message": f"DAG build failed for '{recipe_name}': {exc}",
                "metadata": {"recipe_name": recipe_name},
            })

    # All recipes failed — fatal
    if not dags:
        return {
            "recipe_dags": [],
            "errors": [{
                "node_name": "dag_builder",
                "error_type": ErrorType.DEPENDENCY_RESOLUTION.value,
                "recoverable": False,
                "message": (
                    f"All {len(validated_dicts)} recipes failed DAG construction. "
                    "Cannot schedule."
                ),
                "metadata": {"failed_count": len(validated_dicts)},
            }],
        }

    update: dict = {"recipe_dags": dags}
    if errors:
        update["errors"] = errors
    return update
