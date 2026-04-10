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

# ErrorType is an enum of all possible pipeline error categories (e.g.
# VALIDATION_FAILURE, LLM_ERROR). Used so error_router can make routing
# decisions without string matching.
from app.models.enums import ErrorType

# GRASPState is the shared LangGraph state TypedDict that flows through
# every node. Each node receives it and returns a partial dict of keys to
# update (replace semantics, except "errors" which uses operator.add).
from app.models.pipeline import GRASPState

# EnrichedRecipe: the Pydantic model output from the enricher node.
#   Contains steps with timing, dependencies, and resource requirements.
# ValidatedRecipe: wraps an EnrichedRecipe with validation metadata
#   (passed flag, warnings list, validated_at timestamp). This is what
#   the DAG builder consumes downstream.
from app.models.recipe import EnrichedRecipe, ValidatedRecipe

# Module-level logger — named after the module path so log output is
# filterable by "app.graph.nodes.validator" in production log config.
logger = logging.getLogger(__name__)


async def validator_node(state: GRASPState) -> dict:
    # Pull enriched_recipes out of shared state. These are raw dicts (not
    # Pydantic objects) because LangGraph serialises everything to JSON-
    # compatible dicts between nodes. Defaults to [] if enricher produced
    # nothing (e.g. full enricher failure).
    enriched_dicts: list[dict] = state.get("enriched_recipes", [])

    # Accumulate successfully validated recipes as dicts (re-serialised
    # after wrapping in ValidatedRecipe) to stay serialisation-safe for
    # LangGraph state. The DAG builder will model_validate() these again.
    validated: list[dict] = []

    # Per-recipe recoverable errors collected here. Added to state at the
    # end only if at least one recipe succeeded — otherwise a single fatal
    # error is returned instead (see the `if not validated` block below).
    errors: list[dict] = []

    logger.info("Validating %d enriched recipes", len(enriched_dicts))

    # Process each recipe independently so one bad recipe doesn't abort
    # the entire pipeline — partial success is better than total failure.
    for recipe_dict in enriched_dicts:
        # Extract name for readable log and error messages. Uses nested
        # .get() with a fallback because the dict shape might be malformed
        # if the enricher produced a partial output.
        recipe_name = recipe_dict.get("source", {}).get("name", "unknown")
        try:
            # model_validate() deserialises the raw dict into a fully-typed
            # EnrichedRecipe, running all field_validators and model_validators
            # defined on the model:
            #   - RecipeStep.duration_minutes must be > 0
            #   - RecipeStep.duration_max must be >= duration_minutes
            #   - All step_ids referenced in depends_on must exist in the
            #     same recipe's step list (catches dangling dependency refs)
            # Raises ValidationError on any constraint violation.
            enriched = EnrichedRecipe.model_validate(recipe_dict)

            # Wrap the validated EnrichedRecipe in a ValidatedRecipe envelope.
            # This adds:
            #   - validated_at: UTC timestamp for audit/debugging
            #   - warnings: list for non-fatal advisory notes (empty for now,
            #     reserved for future soft checks like "step seems very long")
            #   - passed: True — explicit flag so downstream nodes can filter
            #     without re-running validation
            validated_recipe = ValidatedRecipe(
                source=enriched,
                validated_at=datetime.now(timezone.utc),
                warnings=[],
                passed=True,
            )

            # Re-serialise to dict so the value is JSON-safe for LangGraph
            # state storage. The DAG builder will call model_validate() again
            # on this dict to get a typed object back.
            validated.append(validated_recipe.model_dump())
            logger.info("Validated recipe: %s", recipe_name)

        except Exception as exc:
            # Catch all exceptions (ValidationError from Pydantic, or any
            # unexpected error from a malformed dict) so one bad recipe
            # cannot crash the whole node.
            logger.warning("Validation failed for '%s': %s", recipe_name, exc)

            # Append a recoverable=True NodeError. LangGraph's error_router
            # checks this flag: recoverable errors let the pipeline continue
            # with the remaining valid recipes; the failed recipe is simply
            # excluded from validated_recipes.
            errors.append(
                {
                    "node_name": "validator",
                    "error_type": ErrorType.VALIDATION_FAILURE.value,
                    "recoverable": True,  # partial failure — pipeline continues
                    "message": f"Validation failed for '{recipe_name}': {exc}",
                    # metadata gives structured context for logging / UI display
                    # without needing to parse the message string.
                    "metadata": {"recipe_name": recipe_name, "error": str(exc)},
                }
            )

    logger.info("Validation complete: %d/%d passed", len(validated), len(enriched_dicts))

    # Guard: if every recipe failed validation there is nothing to schedule.
    # Return a fatal (recoverable=False) error so error_router routes to
    # handle_fatal_error and the pipeline halts rather than producing an
    # empty schedule. The individual recoverable errors are intentionally
    # not included here — the single fatal error is the canonical signal.
    if not validated:
        return {
            "validated_recipes": [],
            "errors": [
                {
                    "node_name": "validator",
                    "error_type": ErrorType.VALIDATION_FAILURE.value,
                    "recoverable": False,  # fatal — no recipes left to schedule
                    "message": "All recipes failed Pydantic validation. Cannot schedule.",
                    "metadata": {"failed_count": len(enriched_dicts)},
                }
            ],
        }

    # At least one recipe passed. Build the state update dict.
    # validated_recipes uses replace semantics (last writer wins in LangGraph),
    # so we always set the full list — never partial appends.
    update: dict = {"validated_recipes": validated}

    # Only include errors key if there were per-recipe failures. GRASPState
    # declares errors with operator.add, so any list we return here gets
    # *appended* to the existing errors list rather than replacing it.
    if errors:
        update["errors"] = errors

    return update
