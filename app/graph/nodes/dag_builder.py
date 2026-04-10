"""
graph/nodes/dag_builder.py
Real DAG builder — Phase 6. Pure algorithmic, no LLM call.

Reads validated_recipes from GRASPState, extracts step dependencies,
builds a NetworkX DiGraph per recipe, validates acyclicity, and returns
RecipeDAG models with edge lists.

Why NetworkX for cycle detection?
  NetworkX's is_directed_acyclic_graph() uses depth-first search and is
  correct for all graph topologies. A hand-rolled DFS would be error-prone —
  the DAG constraint is a hard safety requirement (a cyclic schedule would
  loop forever), so we use a well-tested library rather than custom logic.

Error handling: per-recipe recoverable. If one recipe has a cycle or
invalid dependencies, it is dropped and the pipeline continues with
survivors. If ALL recipes fail, the error is fatal (recoverable=False).

IDEMPOTENCY: Returns recipe_dags as a NEW list (not appended).
Replace semantics — same contract as generator (§2.10). Safe to re-run
on checkpoint resume without producing duplicate DAGs.

Mockable seam:
  _build_single_dag()  — builds one RecipeDAG from a ValidatedRecipe.
  Tests can patch this function to inject failures or verify inputs.

SIMULATE_INTERRUPT:
  Preserved from mock_dag_builder for test_run4 (checkpoint resume test).
  When os.environ.get("SIMULATE_INTERRUPT") == "1", raises RuntimeError
  before completing. LangGraph resumes from validator checkpoint on re-invoke.
  This proves the checkpoint system works — the graph restarts from the
  last successful node (validator) not from scratch (generator).
"""

import logging
import os
import re

import networkx as nx

from app.models.enums import ErrorType
from app.models.pipeline import GRASPState
from app.models.recipe import ValidatedRecipe
from app.models.scheduling import RecipeDAG

logger = logging.getLogger(__name__)


def _generate_recipe_slug(name: str) -> str:
    """Convert recipe name to a URL-safe slug. 'Braised Short Ribs' → 'braised_short_ribs'

    The slug is used as a prefix namespace for step_ids within this recipe.
    When the DAG merger looks up step ownership by step_id prefix, it relies
    on the slug being consistent with how step_ids were generated in the enricher.
    """
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _build_single_dag(validated: ValidatedRecipe) -> RecipeDAG:
    """
    Build a RecipeDAG from a ValidatedRecipe.

    Traverses the composition chain: ValidatedRecipe → EnrichedRecipe → RawRecipe.
    Step dependencies come from EnrichedRecipe.steps[*].depends_on, which the
    enricher node populates when it assigns step_ids and derives prep ordering.

    Two validation checks run before building the NetworkX graph:
      1. Dangling dependency: a step depends on a step_id that doesn't exist.
         Could happen if the enricher generated step_ids inconsistently.
      2. Cycle detection: a dependency cycle (A→B→C→A) would make scheduling
         impossible. NetworkX's simple_cycles() shows the cycle for debugging.

    Raises ValueError on either failure — the caller (dag_builder_node) catches
    this and marks the recipe as recoverable-failed.

    Why store steps=[] in RecipeDAG?
      Steps live in ValidatedRecipe.source.steps (EnrichedRecipe). Duplicating
      them in RecipeDAG would waste checkpoint storage and risk desync if steps
      are ever modified post-enrichment. The merger reads steps from validated_recipes,
      not from recipe_dags — RecipeDAG is purely the edge topology.
    """
    enriched = validated.source
    raw = enriched.source
    slug = _generate_recipe_slug(raw.name)
    steps = enriched.steps
    step_ids = {s.step_id for s in steps}

    # Build edges from depends_on fields.
    # Edge direction: (dependency → dependent), meaning "dependency must complete before dependent".
    # This is the standard DAG convention for task scheduling.
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

    # Validate with NetworkX — construct the DiGraph and check DAG property.
    # add_nodes_from ensures isolated nodes (no dependencies) are included.
    # Without it, steps with no depends_on and no dependents would be missing
    # from the graph and wouldn't participate in critical path calculation.
    G = nx.DiGraph()
    G.add_nodes_from(step_ids)
    G.add_edges_from(edges)

    if not nx.is_directed_acyclic_graph(G):
        cycles = list(nx.simple_cycles(G))
        raise ValueError(f"Cycle(s) detected in '{raw.name}': {cycles}")

    return RecipeDAG(
        recipe_name=raw.name,
        recipe_slug=slug,
        steps=[],  # Steps live in EnrichedRecipe; DAG stores edges only to avoid duplication
        edges=edges,
    )


async def dag_builder_node(state: GRASPState) -> dict:
    """LangGraph node: builds per-recipe dependency DAGs.

    Iterates over validated_recipes, builds one RecipeDAG per recipe,
    and collects both successes (dags) and failures (errors).

    The recipe_name extraction from the nested dict is defensive:
      state["validated_recipes"] → list of dicts (plain Python after checkpoint restore)
      dict["source"]["source"]["name"] → RawRecipe.name in the composition chain
      .get() at each level returns "unknown" if any level is missing.

    All-failed guard: if no DAGs were built, emit a single fatal (recoverable=False)
    error and return empty recipe_dags. The dag_merger_router will route to
    error_router which terminates the pipeline with FAILED status.

    Successful builds: return only the dags list. Partial failures are included
    in the `errors` list (accumulated by operator.add — not replaced).
    """

    # Checkpoint resume test: simulate crash before completing.
    # test_run4 in test_phase3.py sets SIMULATE_INTERRUPT=1, invokes the graph,
    # then re-invokes it. The second invocation resumes from the validator
    # checkpoint and dag_builder runs to completion (interrupt cleared).
    if os.environ.get("SIMULATE_INTERRUPT") == "1":
        raise RuntimeError(
            "SIMULATE_INTERRUPT: dag_builder crashed. LangGraph will resume from validator checkpoint on next invoke."
        )

    validated_dicts = state.get("validated_recipes", [])

    dags: list[dict] = []
    errors: list[dict] = []

    for recipe_dict in validated_dicts:
        # Navigate the nested dict structure to get the recipe name for logging.
        # model_validate() below re-parses the full structure — this is just for the error message.
        recipe_name = recipe_dict.get("source", {}).get("source", {}).get("name", "unknown")
        try:
            # model_validate() re-parses the dict into typed Pydantic models.
            # Required because state comes back from checkpoint as plain dicts,
            # not Pydantic instances. See §LangGraph serialization trap.
            validated = ValidatedRecipe.model_validate(recipe_dict)
            dag = _build_single_dag(validated)
            dags.append(dag.model_dump())
            logger.info("DAG built for '%s': %d edges", recipe_name, len(dag.edges))
        except Exception as exc:
            # Per-recipe recoverable failure. The recipe is dropped but the
            # pipeline continues with remaining recipes.
            logger.warning("DAG build failed for '%s': %s", recipe_name, exc)
            errors.append(
                {
                    "node_name": "dag_builder",
                    "error_type": ErrorType.DEPENDENCY_RESOLUTION.value,
                    "recoverable": True,
                    "message": f"DAG build failed for '{recipe_name}': {exc}",
                    "metadata": {"recipe_name": recipe_name},
                }
            )

    # All recipes failed — upgrade to fatal error. The pipeline cannot schedule
    # anything without at least one valid DAG.
    if not dags:
        return {
            "recipe_dags": [],
            "errors": [
                {
                    "node_name": "dag_builder",
                    "error_type": ErrorType.DEPENDENCY_RESOLUTION.value,
                    "recoverable": False,
                    "message": (f"All {len(validated_dicts)} recipes failed DAG construction. Cannot schedule."),
                    "metadata": {"failed_count": len(validated_dicts)},
                }
            ],
        }

    # Return the successful DAGs. Include per-recipe errors if any recipes failed.
    # GRASPState.errors uses operator.add accumulator — partial errors are appended,
    # not replacing any previously accumulated errors from earlier nodes.
    update: dict = {"recipe_dags": dags}
    if errors:
        update["errors"] = errors
    return update
