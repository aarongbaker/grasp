"""
graph/nodes/mock_dag_merger.py
Mock DAG merger. Returns the hand-resolved fixture MergedDAG.
Deleted in Phase 6.

test_mode="fatal_error":
  Appends NodeError(recoverable=False) — RESOURCE_CONFLICT.
  error_router sees recoverable=False → "fatal" → handle_fatal_error → END.
  Pipeline halts. No schedule produced. This tests Run 3 (Fatal Error).

Normal path:
  Returns the hand-resolved MergedDAG fixture. Phase 6 will validate
  real merger output against this same fixture as the known-correct answer.
"""

from models.pipeline import GRASPState
from models.enums import ErrorType
from tests.fixtures.schedules import MERGED_DAG_FULL, MERGED_DAG_TWO_RECIPE


async def dag_merger_node(state: GRASPState) -> dict:
    test_mode = state.get("test_mode")

    if test_mode == "fatal_error":
        # RESOURCE_CONFLICT — unrecoverable. No partial schedule possible.
        return {
            "errors": [{
                "node_name": "dag_merger",
                "error_type": ErrorType.RESOURCE_CONFLICT.value,
                "recoverable": False,
                "message": "OVEN resource conflict: braise and fondant bake overlap with max_oven_racks=1",
                "metadata": {
                    "resource": "oven",
                    "conflicting_step_ids": [
                        "short_rib_step_3",
                        "fondant_step_3",
                    ],
                },
            }]
        }

    # Determine which fixture to return based on how many DAGs made it through
    recipe_dags = state.get("recipe_dags", [])
    if len(recipe_dags) >= 3:
        merged = MERGED_DAG_FULL
    else:
        merged = MERGED_DAG_TWO_RECIPE

    return {"merged_dag": merged.model_dump()}
