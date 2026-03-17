"""
graph/nodes/generator.py
Real recipe generator — Phase 4. First LLM call in the system.

Reads DinnerConcept + KitchenConfig + Equipment from GRASPState,
calls Claude via LangChain structured output, returns List[RawRecipe].

Error handling: generator failure is always fatal (recoverable=False).
Nothing can be enriched or scheduled without recipes.

IDEMPOTENCY: Returns raw_recipes as a NEW list (not appended to existing).
Replace semantics — if this node runs twice on resume, the state has N
recipes, not 2N. This is the contract from §2.10.

Mockable seam: _create_llm() is extracted so tests can patch it to bypass
the real Claude API while still exercising all node logic.
"""

import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from core.llm import extract_token_usage, is_timeout_error, llm_retry
from core.settings import get_settings
from models.enums import ErrorType, MealType, Occasion
from models.errors import NodeError
from models.pipeline import DinnerConcept, GRASPState
from models.recipe import RawRecipe

logger = logging.getLogger(__name__)

# ── Structured output wrapper ────────────────────────────────────────────────


class RecipeGenerationOutput(BaseModel):
    """Wrapper for LangChain structured output. Claude returns this shape."""

    recipes: list[RawRecipe]


# ── Recipe count derivation ──────────────────────────────────────────────────

RECIPE_COUNT_MAP: dict[tuple[MealType, Occasion], int] = {
    # Casual — simple meals
    (MealType.BREAKFAST, Occasion.CASUAL): 1,
    (MealType.BRUNCH, Occasion.CASUAL): 2,
    (MealType.LUNCH, Occasion.CASUAL): 1,
    (MealType.DINNER, Occasion.CASUAL): 2,
    (MealType.APPETIZERS, Occasion.CASUAL): 2,
    (MealType.SNACKS, Occasion.CASUAL): 2,
    (MealType.DESSERT, Occasion.CASUAL): 1,
    (MealType.MEAL_PREP, Occasion.CASUAL): 3,
    # Dinner party — multi-course
    (MealType.BREAKFAST, Occasion.DINNER_PARTY): 2,
    (MealType.BRUNCH, Occasion.DINNER_PARTY): 3,
    (MealType.LUNCH, Occasion.DINNER_PARTY): 2,
    (MealType.DINNER, Occasion.DINNER_PARTY): 3,
    (MealType.APPETIZERS, Occasion.DINNER_PARTY): 3,
    (MealType.SNACKS, Occasion.DINNER_PARTY): 3,
    (MealType.DESSERT, Occasion.DINNER_PARTY): 2,
    (MealType.MEAL_PREP, Occasion.DINNER_PARTY): 3,
    # Tasting menu — many small courses
    (MealType.BREAKFAST, Occasion.TASTING_MENU): 3,
    (MealType.BRUNCH, Occasion.TASTING_MENU): 5,
    (MealType.LUNCH, Occasion.TASTING_MENU): 4,
    (MealType.DINNER, Occasion.TASTING_MENU): 5,
    (MealType.APPETIZERS, Occasion.TASTING_MENU): 5,
    (MealType.SNACKS, Occasion.TASTING_MENU): 4,
    (MealType.DESSERT, Occasion.TASTING_MENU): 3,
    (MealType.MEAL_PREP, Occasion.TASTING_MENU): 3,
    # Meal prep — batch cooking
    (MealType.BREAKFAST, Occasion.MEAL_PREP): 3,
    (MealType.BRUNCH, Occasion.MEAL_PREP): 3,
    (MealType.LUNCH, Occasion.MEAL_PREP): 3,
    (MealType.DINNER, Occasion.MEAL_PREP): 4,
    (MealType.APPETIZERS, Occasion.MEAL_PREP): 3,
    (MealType.SNACKS, Occasion.MEAL_PREP): 4,
    (MealType.DESSERT, Occasion.MEAL_PREP): 3,
    (MealType.MEAL_PREP, Occasion.MEAL_PREP): 4,
}

DEFAULT_RECIPE_COUNT = 3


def _derive_recipe_count(meal_type: MealType, occasion: Occasion) -> int:
    """Lookup target recipe count from meal_type + occasion."""
    return RECIPE_COUNT_MAP.get((meal_type, occasion), DEFAULT_RECIPE_COUNT)


# ── Prompt builders ──────────────────────────────────────────────────────────


def _format_dietary_restrictions(restrictions: list[str]) -> str:
    if not restrictions:
        return "None specified."
    return "\n".join(f"- {r}" for r in restrictions)


def _format_equipment(equipment: list[dict]) -> str:
    if not equipment:
        return "Standard home kitchen equipment."
    lines = []
    for e in equipment:
        name = e.get("name", "Unknown")
        category = e.get("category", "")
        techniques = e.get("unlocks_techniques", [])
        if techniques:
            lines.append(f"- {name} ({category}) — unlocks: {', '.join(techniques)}")
        else:
            lines.append(f"- {name} ({category})")
    return "\n".join(lines)


def _build_system_prompt(
    concept: DinnerConcept,
    kitchen_config: dict,
    equipment: list[dict],
    recipe_count: int,
) -> str:
    restrictions = _format_dietary_restrictions(concept.dietary_restrictions)
    equip_text = _format_equipment(equipment)

    max_burners = kitchen_config.get("max_burners", 4)
    max_oven_racks = kitchen_config.get("max_oven_racks", 2)
    has_second_oven = kitchen_config.get("has_second_oven", False)

    return f"""You are GRASP, an expert chef assistant that designs cohesive multi-course meal plans.
Your recipes are written for experienced home cooks who value precision, technique, and timing.

## DINNER CONCEPT
"{concept.free_text}"

## MENU PARAMETERS
- Meal type: {concept.meal_type.value}
- Occasion: {concept.occasion.value}
- Guest count: {concept.guest_count}
- Number of courses to generate: {recipe_count}

## DIETARY RESTRICTIONS
{restrictions}

## KITCHEN CONSTRAINTS
- Stovetop burners available: {max_burners}
- Oven racks available: {max_oven_racks}
- Second oven: {"Yes" if has_second_oven else "No"}

## AVAILABLE EQUIPMENT
{equip_text}

## GUIDELINES
1. Generate exactly {recipe_count} recipes forming a balanced, cohesive {concept.occasion.value} {concept.meal_type.value} menu.
2. Scale all ingredient quantities for {concept.guest_count} servings.
3. Write clear, detailed steps with temperatures (Celsius), times, and visual doneness cues.
4. STRICTLY respect all dietary restrictions. Never include restricted ingredients or derivatives.
5. Design recipes that work within the kitchen's burner and oven rack limits.
6. If the concept mentions specific dishes, include them. Fill remaining courses to complement.
7. Include cuisine attribution for each recipe.
8. Provide realistic estimated_total_minutes for each recipe (prep through plating).
9. Use the available equipment to unlock advanced techniques where appropriate.
10. Each recipe must have at least 3 steps. Steps should be detailed enough for an intermediate cook."""


# ── LLM factory (mockable seam) ─────────────────────────────────────────────


def _create_llm() -> ChatAnthropic:
    """
    Creates the ChatAnthropic instance. Extracted as a separate function so
    tests can patch graph.nodes.generator._create_llm to bypass the real API.
    """
    settings = get_settings()
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        api_key=settings.anthropic_api_key,
        max_tokens=4096,
    )


# ── Node function ────────────────────────────────────────────────────────────


async def recipe_generator_node(state: GRASPState) -> dict:
    """
    Real recipe generator node. Calls Claude to generate RawRecipe objects.

    Returns partial GRASPState dict with raw_recipes (replace semantics).
    On failure, returns empty raw_recipes + fatal NodeError.
    """
    try:
        # Read state — validate concept as DinnerConcept
        concept = DinnerConcept.model_validate(state["concept"])
        kitchen_config = state.get("kitchen_config", {})
        equipment = state.get("equipment", [])

        # Derive recipe count from concept
        recipe_count = _derive_recipe_count(concept.meal_type, concept.occasion)

        # Build prompt
        system_prompt = _build_system_prompt(concept, kitchen_config, equipment, recipe_count)

        # Call Claude via structured output (with retry on transient errors)
        llm = _create_llm()
        chain = llm.with_structured_output(RecipeGenerationOutput)

        @llm_retry
        async def _invoke_llm():
            return await chain.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(
                        content=f"Generate {recipe_count} recipes for this {concept.occasion.value} {concept.meal_type.value}."
                    ),
                ]
            )

        logger.info("Generating %d recipes for %s %s", recipe_count, concept.occasion.value, concept.meal_type.value)
        result = await _invoke_llm()

        logger.info("Generated %d recipes: %s", len(result.recipes), [r.name for r in result.recipes])
        # Return raw_recipes as dicts (replace semantics)
        usage = extract_token_usage(result, "recipe_generator")
        return {
            "raw_recipes": [r.model_dump() for r in result.recipes],
            "token_usage": [usage],
        }

    except Exception as exc:
        # Classify error type
        exc_type = type(exc).__name__
        if is_timeout_error(exc):
            error_type = ErrorType.LLM_TIMEOUT
        else:
            error_type = ErrorType.LLM_PARSE_FAILURE

        logger.error("Generator failed: %s: %s", exc_type, exc)

        error = NodeError(
            node_name="recipe_generator",
            error_type=error_type,
            recoverable=False,
            message=f"{exc_type}: {exc}",
            metadata={"exception_type": exc_type},
        )

        return {
            "raw_recipes": [],
            "errors": [error.model_dump()],
        }
