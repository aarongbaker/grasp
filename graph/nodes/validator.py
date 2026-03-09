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

from datetime import datetime, timezone
from models.pipeline import GRASPState
from models.recipe import EnrichedRecipe, ValidatedRecipe
from models.enums import ErrorType


async def validator_node(state: GRASPState) -> dict:
    enriched_dicts: list[dict] = state.get("enriched_recipes", [])
    validated: list[dict] = []
    errors: list[dict] = []

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

        except Exception as exc:
            # Per-recipe recoverable failure
            errors.append({
                "node_name": "validator",
                "error_type": ErrorType.VALIDATION_FAILURE.value,
                "recoverable": True,
                "message": f"Validation failed for '{recipe_name}': {exc}",
                "metadata": {"recipe_name": recipe_name, "error": str(exc)},
            })

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
