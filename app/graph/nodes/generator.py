"""
graph/nodes/generator.py
Real recipe generator — Phase 4. First LLM call in the system.

Reads DinnerConcept + KitchenConfig + Equipment from GRASPState,
calls Claude via LangChain structured output, returns List[RawRecipe].

Cookbook-mode sessions are deterministic: persisted selected cookbook chunks
are converted into downstream-compatible RawRecipe objects and skip the LLM
free-text generation path entirely.

Error handling: generator failure is always fatal (recoverable=False).
Nothing can be enriched or scheduled without recipes.

IDEMPOTENCY: Returns raw_recipes as a NEW list (not appended to existing).
Replace semantics — if this node runs twice on resume, the state has N
recipes, not 2N. This is the contract from §2.10.

Mockable seam: _create_llm() is extracted so tests can patch it to bypass
the real Claude API while still exercising all node logic.
"""

import logging
import re

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.core.llm import extract_token_usage, is_timeout_error, llm_retry
from app.core.settings import get_settings
from app.models.enums import ErrorType, MealType, Occasion
from app.models.errors import NodeError
from app.models.errors import NodeError
from app.models.pipeline import DinnerConcept, GRASPState, SelectedCookbookRecipe
from app.models.recipe import Ingredient, RawRecipe

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
\"{concept.free_text}\"

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


# ── Cookbook-mode deterministic parsing ─────────────────────────────────────


def _strip_markdown_heading_prefix(line: str) -> str:
    return re.sub(r"^#{1,6}\s*", "", line).strip()


def _normalise_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _extract_recipe_name(selection: SelectedCookbookRecipe) -> str:
    for line in _normalise_lines(selection.text):
        cleaned = _strip_markdown_heading_prefix(line)
        if cleaned and not cleaned.lower().startswith(("ingredients", "method", "directions", "steps")):
            return cleaned[:200]
    fallback = selection.chapter.strip() or f"Cookbook recipe p.{selection.page_number}"
    return fallback[:200]


def _extract_ingredient_lines(lines: list[str]) -> list[str]:
    ingredients: list[str] = []
    in_ingredients = False
    for line in lines:
        lowered = _strip_markdown_heading_prefix(line).lower().rstrip(":")
        if lowered in {"ingredients", "for the ingredients"}:
            in_ingredients = True
            continue
        if lowered in {"method", "directions", "steps", "preparation"}:
            break
        if in_ingredients:
            ingredients.append(line)
    return ingredients


def _extract_step_lines(lines: list[str]) -> list[str]:
    steps: list[str] = []
    in_steps = False
    for line in lines:
        cleaned = _strip_markdown_heading_prefix(line)
        lowered = cleaned.lower().rstrip(":")
        if lowered in {"method", "directions", "steps", "preparation"}:
            in_steps = True
            continue
        if in_steps:
            steps.append(re.sub(r"^(?:\d+[\.)]|[-*•])\s*", "", cleaned).strip())
    return [step for step in steps if step]


def _parse_ingredient(line: str) -> Ingredient:
    cleaned = re.sub(r"^(?:[-*•])\s*", "", line).strip()
    if not cleaned:
        raise ValueError("Empty ingredient line")

    if " – " in cleaned:
        quantity, name = cleaned.split(" – ", 1)
    elif " - " in cleaned:
        quantity, name = cleaned.split(" - ", 1)
    else:
        parts = cleaned.split()
        if len(parts) >= 3 and any(ch.isdigit() for ch in parts[0]):
            quantity = " ".join(parts[:2])
            name = " ".join(parts[2:])
        elif len(parts) >= 2 and any(ch.isdigit() for ch in parts[0]):
            quantity = parts[0]
            name = " ".join(parts[1:])
        else:
            quantity = "as needed"
            name = cleaned

    return Ingredient(name=name.strip()[:200], quantity=quantity.strip()[:100] or "as needed")


def _estimate_minutes(step_count: int) -> int:
    return max(15, step_count * 15)


def _build_cookbook_raw_recipe(selection: SelectedCookbookRecipe, guest_count: int) -> RawRecipe:
    lines = _normalise_lines(selection.text)
    if not lines:
        raise ValueError(f"Selected cookbook chunk {selection.chunk_id} has no text")

    steps = _extract_step_lines(lines)
    if len(steps) < 3:
        raise ValueError(
            f"Selected cookbook chunk {selection.chunk_id} did not contain at least 3 method steps needed for scheduling"
        )

    ingredient_lines = _extract_ingredient_lines(lines)
    ingredients = [_parse_ingredient(line) for line in ingredient_lines if line.strip()]
    if not ingredients:
        ingredients = [Ingredient(name="See cookbook source text", quantity="as needed")]

    return RawRecipe(
        name=_extract_recipe_name(selection),
        description=(
            f"Cookbook-selected recipe from {selection.book_title}"
            + (f", chapter {selection.chapter}" if selection.chapter else "")
            + (f", page {selection.page_number}" if selection.page_number else "")
            + "."
        ),
        servings=guest_count,
        cuisine=f"Cookbook: {selection.book_title}"[:200],
        estimated_total_minutes=_estimate_minutes(len(steps)),
        ingredients=ingredients,
        steps=steps,
    )


def build_cookbook_raw_recipes(concept: DinnerConcept) -> list[RawRecipe]:
    if concept.concept_source != "cookbook":
        return []
    return [_build_cookbook_raw_recipe(selection, concept.guest_count) for selection in concept.selected_recipes]


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

        if concept.concept_source == "cookbook":
            cookbook_recipes = build_cookbook_raw_recipes(concept)
            logger.info("Seeded %d cookbook recipes from persisted selections", len(cookbook_recipes))
            return {
                "raw_recipes": [recipe.model_dump(mode="json") for recipe in cookbook_recipes],
            }

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
        # Preserve cookbook parse failures as explicit validation failures.
        exc_type = type(exc).__name__
        if is_timeout_error(exc):
            error_type = ErrorType.LLM_TIMEOUT
        elif isinstance(exc, ValueError):
            error_type = ErrorType.VALIDATION_FAILURE
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
            "errors": [error.model_dump(mode="json")],
        }
