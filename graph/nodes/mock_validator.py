"""
graph/nodes/mock_validator.py
THE CRITICAL MOCK — this is NOT a stub.

mock_validator.py runs REAL Pydantic validators against the enriched recipes
in state. It instantiates actual EnrichedRecipe models from the state dicts,
which triggers:
  - RecipeStep.duration_minutes > 0 (field_validator)
  - EnrichedRecipe.depends_on consistency (model_validator)
  - RecipeStep.duration_max >= duration_minutes (field_validator)

This means fixture data is validated against the same schema the real
pipeline will use. Any fixture inconsistency is caught here in Phase 3,
not discovered in Phase 5 when real data flows through.

Per-recipe failure handling:
  If one recipe's EnrichedRecipe fails validation:
    → append VALIDATION_FAILURE NodeError (recoverable=True)
    → exclude that recipe from validated_recipes
    → continue (error_router sees recoverable=True)
  If ALL recipes fail validation:
    → append VALIDATION_FAILURE NodeError (recoverable=False)
    → error_router routes to handle_fatal_error

Deleted in Phase 5 and replaced by the real validator (which does the
same thing — the mock already IS the real validator logic).
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
            # THIS IS THE REAL VALIDATOR. Not a stub.
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
        # Replace any per-recipe recoverable errors with a single fatal error
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
