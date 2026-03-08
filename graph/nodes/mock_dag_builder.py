"""
graph/nodes/mock_dag_builder.py
Mock DAG builder. Returns fixture RecipeDAGs.
Deleted in Phase 6.

SIMULATE_INTERRUPT test mode:
  When os.environ.get("SIMULATE_INTERRUPT") == "1", raises RuntimeError
  to simulate a mid-pipeline crash. LangGraph saves checkpoint AFTER
  validator completes. The interrupt prevents dag_builder from completing,
  so no dag_builder checkpoint is saved. On re-invoke with same thread_id,
  LangGraph resumes from the validator checkpoint and re-runs dag_builder.
  This proves the idempotency contract: generator/enricher/validator each
  ran exactly once across both invocations.

Per-recipe recoverable failure (test_mode="per_recipe_dag_fail"):
  Not used in Phase 3 tests. Available for Phase 6 testing.

test_mode="fatal_error":
  dag_merger handles the fatal error, not dag_builder.
"""

import os
from models.pipeline import GRASPState
from models.enums import ErrorType
from tests.fixtures.schedules import RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT


async def dag_builder_node(state: GRASPState) -> dict:
    # Resume test: simulate crash before this node completes
    if os.environ.get("SIMULATE_INTERRUPT") == "1":
        raise RuntimeError(
            "SIMULATE_INTERRUPT: dag_builder crashed. "
            "LangGraph will resume from validator checkpoint on next invoke."
        )

    validated = state.get("validated_recipes", [])
    recipe_names = {v.get("source", {}).get("source", {}).get("name", "") for v in validated}

    # Build DAGs only for recipes that survived validation
    dags = []
    if any("Short Rib" in n or "short_rib" in n for n in recipe_names) or len(validated) >= 1:
        dags.append(RECIPE_DAG_SHORT_RIBS.model_dump())
    if any("Pommes" in n or "pommes" in n for n in recipe_names) or len(validated) >= 2:
        dags.append(RECIPE_DAG_POMMES_PUREE.model_dump())
    if any("Fondant" in n or "fondant" in n for n in recipe_names) or len(validated) >= 3:
        dags.append(RECIPE_DAG_FONDANT.model_dump())

    # If fewer validated recipes (recoverable_error test), build fewer DAGs
    dags = dags[:len(validated)]

    return {"recipe_dags": dags}
