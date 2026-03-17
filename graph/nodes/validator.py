"""
graph/nodes/validator.py
Real validator — Phase 5. Promoted from mock_validator.py.

This is NOT a new implementation. The mock already ran REAL Pydantic
validation against EnrichedRecipe. The code is identical — only the
file location and import path changed.

Runs EnrichedRecipe.model_validate() on each recipe dict in state,
which triggers:
  - RecipeStep.duration_minutes > 0 (field_validator)
  - RecipeStep.duration_max >= duration_minutes (field_validator)
  - depends_on consistency — all referenced step_ids exist (model_validator)

Per-recipe failure handling:
  If one recipe fails validation:
    → append VALIDATION_FAILURE NodeError (recoverable=True)
    → exclude that recipe from validated_recipes
    → continue (error_router sees recoverable=True)
  If ALL recipes fail validation:
    → append VALIDATION_FAILURE NodeError (recoverable=False)
    → error_router routes to handle_fatal_error
"""

import logging
from datetime import datetime, timezone

from models.enums import ErrorType
from models.pipeline import GRASPState
from models.recipe import EnrichedRecipe, ValidatedRecipe

logger = logging.getLogger(__name__)


async def validator_node(state: GRASPState) -> dict:
    enriched_dicts: list[dict] = state.get("enriched_recipes", [])
    validated: list[dict] = []
    errors: list[dict] = []

    logger.info("Validating %d enriched recipes", len(enriched_dicts))

    for recipe_dict in enriched_dicts:
        recipe_name = recipe_dict.get("source", {}).get("name", "unknown")
        try:
            enriched = EnrichedRecipe.model_validate(recipe_dict)

            validated_recipe = ValidatedRecipe(
                source=enriched,
                validated_at=datetime.now(timezone.utc),
                warnings=[],
                passed=True,
            )
            validated.append(validated_recipe.model_dump())
            logger.info("Validated recipe: %s", recipe_name)

        except Exception as exc:
            logger.warning("Validation failed for '%s': %s", recipe_name, exc)
            # Per-recipe recoverable failure
            errors.append({
                "node_name": "validator",
                "error_type": ErrorType.VALIDATION_FAILURE.value,
                "recoverable": True,
                "message": f"Validation failed for '{recipe_name}': {exc}",
                "metadata": {"recipe_name": recipe_name, "error": str(exc)},
            })

    logger.info("Validation complete: %d/%d passed", len(validated), len(enriched_dicts))

    if not validated:
        # All recipes failed — fatal
        return {
            "validated_recipes": [],
            "errors": [{
                "node_name": "validator",
                "error_type": ErrorType.VALIDATION_FAILURE.value,
                "recoverable": False,
                "message": "All recipes failed Pydantic validation. Cannot schedule.",
                "metadata": {"failed_count": len(enriched_dicts)},
            }],
        }

    update: dict = {"validated_recipes": validated}
    if errors:
        update["errors"] = errors
    return update
