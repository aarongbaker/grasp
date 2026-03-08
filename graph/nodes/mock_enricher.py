"""
graph/nodes/mock_enricher.py
Mock RAG enricher. Converts fixture RawRecipes to EnrichedRecipes.
Deleted in Phase 5 along with mock_validator.py.

test_mode="recoverable_error":
  Simulates per-recipe failure on fondant. Returns only 2 enriched recipes.
  Appends NodeError(recoverable=True) for the failed recipe.
  error_router sees recoverable=True → "continue" → pipeline finishes PARTIAL.

test_mode=None:
  Returns all 3 enriched recipes. No errors.

IDEMPOTENCY: Replace semantics on enriched_recipes.
"""

from datetime import datetime
from models.pipeline import GRASPState
from models.enums import ErrorType
from tests.fixtures.recipes import (
    ENRICHED_SHORT_RIBS,
    ENRICHED_POMMES_PUREE,
    ENRICHED_CHOCOLATE_FONDANT,
)


async def rag_enricher_node(state: GRASPState) -> dict:
    test_mode = state.get("test_mode")

    if test_mode == "recoverable_error":
        # Fondant fails enrichment. Drop it. Return partial result + error.
        return {
            "enriched_recipes": [
                ENRICHED_SHORT_RIBS.model_dump(),
                ENRICHED_POMMES_PUREE.model_dump(),
                # ENRICHED_CHOCOLATE_FONDANT deliberately excluded
            ],
            "errors": [{
                "node_name": "rag_enricher",
                "error_type": ErrorType.RAG_FAILURE.value,
                "recoverable": True,
                "message": "RAG retrieval returned zero results for Chocolate Fondant",
                "metadata": {
                    "query": "chocolate fondant technique",
                    "chunk_type": "TECHNIQUE",
                    "index_name": "grasp-cookbooks",
                },
            }],
        }

    # Happy path — all 3 recipes enriched
    return {
        "enriched_recipes": [
            ENRICHED_SHORT_RIBS.model_dump(),
            ENRICHED_POMMES_PUREE.model_dump(),
            ENRICHED_CHOCOLATE_FONDANT.model_dump(),
        ]
    }
