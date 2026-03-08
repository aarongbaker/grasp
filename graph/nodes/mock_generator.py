"""
graph/nodes/mock_generator.py
Mock recipe generator — returns fixture data as partial GRASPState dict.
Deleted in Phase 4 and replaced by graph/nodes/generator.py.

test_mode behaviour:
  None                  → happy path, return all 3 fixture recipes
  "recoverable_error"   → normal return (enricher handles this test_mode)
  "fatal_error"         → normal return (dag_merger handles this test_mode)
  "simulate_interrupt"  → checked here via env var in the dag_builder mock

IDEMPOTENCY: Returns raw_recipes as a NEW list (not appended to existing).
Replace semantics — if this node runs twice on resume, the state has 3
recipes, not 6. This is the contract from §2.10.
"""

from models.pipeline import GRASPState
from tests.fixtures.recipes import (
    RAW_SHORT_RIBS,
    RAW_POMMES_PUREE,
    RAW_CHOCOLATE_FONDANT,
)


async def recipe_generator_node(state: GRASPState) -> dict:
    """Returns all 3 fixture recipes. Replace semantics on raw_recipes."""
    return {
        "raw_recipes": [
            RAW_SHORT_RIBS.model_dump(),
            RAW_POMMES_PUREE.model_dump(),
            RAW_CHOCOLATE_FONDANT.model_dump(),
        ]
    }
