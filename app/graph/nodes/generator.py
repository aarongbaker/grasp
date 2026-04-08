"""
graph/nodes/generator.py
Real recipe generator — Phase 4. First LLM call in the system.

Reads DinnerConcept + KitchenConfig + Equipment from GRASPState,
calls Claude via LangChain structured output, returns List[RawRecipe].

Cookbook-mode sessions are deterministic: persisted selected cookbook chunks
are converted into downstream-compatible RawRecipe objects and skip the LLM
free-text generation path entirely.

Authored-mode sessions are also deterministic: the persisted authored selection
is resolved against the user's saved recipe library, compiled through the
native authored-recipe model seam, and returned as one RawRecipe without
calling the LLM.

Planner-authored-anchor sessions are mixed-origin: one persisted authored
anchor is compiled deterministically, then the LLM is asked only for the
remaining complementary recipes needed to fill the meal/occasion count.

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
import uuid

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import select

from app.core.llm import extract_token_usage, is_timeout_error, llm_retry
from app.core.settings import get_settings
from app.models.authored_recipe import AuthoredRecipeCreate, AuthoredRecipeRecord
from app.models.enums import ErrorType, MealType, Occasion
from app.models.errors import NodeError
from app.models.pipeline import (
    DinnerConcept,
    GRASPState,
    PlannerLibraryAuthoredRecipeAnchor,
    PlannerLibraryCookbookTarget,
    PlannerLibraryCookbookPlanningMode,
    SelectedAuthoredRecipe,
    SelectedCookbookRecipe,
)
from app.models.recipe import Ingredient, RawRecipe, RecipeProvenance

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

    oven_guidance = _build_oven_compatibility_prompt_guidance(has_second_oven=has_second_oven)

    return f'''You are GRASP, an expert chef assistant that designs cohesive multi-course meal plans.
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
10. Each recipe must have at least 3 steps. Steps should be detailed enough for an intermediate cook.
11. {oven_guidance}'''


def _build_mixed_origin_system_prompt(
    concept: DinnerConcept,
    kitchen_config: dict,
    equipment: list[dict],
    anchor_recipe: RawRecipe,
    complement_count: int,
) -> str:
    restrictions = _format_dietary_restrictions(concept.dietary_restrictions)
    equip_text = _format_equipment(equipment)

    max_burners = kitchen_config.get("max_burners", 4)
    max_oven_racks = kitchen_config.get("max_oven_racks", 2)
    has_second_oven = kitchen_config.get("has_second_oven", False)
    oven_guidance = _build_oven_compatibility_prompt_guidance(has_second_oven=has_second_oven)

    return f'''You are GRASP, an expert chef assistant that designs cohesive multi-course meal plans.
Your recipes are written for experienced home cooks who value precision, technique, and timing.

## DINNER CONCEPT
"{concept.free_text}"

## MENU PARAMETERS
- Meal type: {concept.meal_type.value}
- Occasion: {concept.occasion.value}
- Guest count: {concept.guest_count}
- Number of complementary courses to generate: {complement_count}

## FIXED ANCHOR RECIPE (ALREADY CHOSEN — DO NOT REGENERATE OR RENAME)
- Name: {anchor_recipe.name}
- Description: {anchor_recipe.description}
- Cuisine: {anchor_recipe.cuisine}
- Servings: {anchor_recipe.servings}
- Estimated total minutes: {anchor_recipe.estimated_total_minutes}
- Key ingredients: {", ".join(ingredient.name for ingredient in anchor_recipe.ingredients[:8]) or "See anchored recipe"}

## DIETARY RESTRICTIONS
{restrictions}

## KITCHEN CONSTRAINTS
- Stovetop burners available: {max_burners}
- Oven racks available: {max_oven_racks}
- Second oven: {"Yes" if has_second_oven else "No"}

## AVAILABLE EQUIPMENT
{equip_text}

## GUIDELINES
1. Generate exactly {complement_count} NEW recipes that complement the fixed anchor recipe above.
2. Do not regenerate, rename, restate, or paraphrase the anchor recipe as one of the outputs.
3. The resulting menu should feel balanced and cohesive around the anchor recipe.
4. Scale all ingredient quantities for {concept.guest_count} servings.
5. Write clear, detailed steps with temperatures (Celsius), times, and visual doneness cues.
6. STRICTLY respect all dietary restrictions. Never include restricted ingredients or derivatives.
7. Design recipes that work within the kitchen's burner and oven rack limits alongside the anchor.
8. Include cuisine attribution for each generated recipe.
9. Provide realistic estimated_total_minutes for each generated recipe (prep through plating).
10. Each generated recipe must have at least 3 steps. Steps should be detailed enough for an intermediate cook.
11. {oven_guidance}'''


def _build_oven_compatibility_prompt_guidance(*, has_second_oven: bool) -> str:
    if has_second_oven:
        return (
            "Prefer menus with naturally compatible oven workloads, but a second oven means "
            "parallel dishes may use meaningfully different temperatures when needed."
        )

    return (
        "For single-oven kitchens, prefer recipe combinations whose oven-temperature windows are naturally compatible. "
        "Treat oven temperatures within about 15°F of each other as compatible, avoid pairing overlapping long low braises "
        "with high-heat bakes or desserts unless timing can be serialized cleanly, and if tension remains, prefer menus with "
        "only one oven-heavy dish plus stovetop/passive complements."
    )


def _build_human_prompt(recipe_count: int, concept: DinnerConcept) -> str:
    return (
        f"Generate exactly {recipe_count} recipes for this menu. "
        f"The menu should satisfy the concept {concept.free_text!r} and return only the structured recipe payload."
    )



def _build_mixed_origin_human_prompt(complement_count: int, anchor_recipe: RawRecipe) -> str:
    return (
        f"Generate exactly {complement_count} complementary recipes around the fixed anchor recipe {anchor_recipe.name!r}. "
        "Do not repeat the anchor recipe and return only the structured recipe payload."
    )



def _extract_oven_step_temperatures(recipe: RawRecipe) -> list[int]:
    temperatures: list[int] = []
    for step in recipe.steps:
        lower_step = step.lower()
        if "oven" not in lower_step and "bake" not in lower_step and "roast" not in lower_step and "broil" not in lower_step:
            continue

        fahrenheit_match = re.search(r"(\d{3})\s*°?\s*f", lower_step)
        if fahrenheit_match:
            temperatures.append(int(fahrenheit_match.group(1)))
            continue

        celsius_match = re.search(r"(\d{2,3})\s*°?\s*c", lower_step)
        if celsius_match:
            celsius = int(celsius_match.group(1))
            temperatures.append(round((celsius * 9 / 5) + 32))
            continue

        vague_heat_map = {
            "high heat": 437,
            "hot oven": 437,
            "medium heat": 362,
            "moderate oven": 362,
            "low heat": 312,
            "low oven": 312,
        }
        for phrase, temp_f in vague_heat_map.items():
            if phrase in lower_step:
                temperatures.append(temp_f)
                break

    return temperatures


def _score_menu_oven_compatibility(*, recipes: list[RawRecipe], has_second_oven: bool) -> dict:
    tolerance_f = 15
    oven_heavy_recipe_count = 0
    temperatures_by_recipe: dict[str, list[int]] = {}
    incompatible_pairs: list[tuple[str, str, int, int]] = []
    missing_temperature_recipes: list[str] = []

    for recipe in recipes:
        temps = _extract_oven_step_temperatures(recipe)
        temperatures_by_recipe[recipe.name] = temps
        if temps:
            oven_heavy_recipe_count += 1
        else:
            missing_temperature_recipes.append(recipe.name)

    if has_second_oven:
        score = 0
    else:
        score = max(0, oven_heavy_recipe_count - 1) * 20

    recipe_items = list(temperatures_by_recipe.items())
    for index, (recipe_a, temps_a) in enumerate(recipe_items):
        for recipe_b, temps_b in recipe_items[index + 1 :]:
            if not temps_a or not temps_b:
                continue

            min_gap = min(abs(temp_a - temp_b) for temp_a in temps_a for temp_b in temps_b)
            if min_gap > tolerance_f:
                incompatible_pairs.append((recipe_a, recipe_b, temps_a[0], temps_b[0]))
                if not has_second_oven:
                    score += 50
            elif not has_second_oven and oven_heavy_recipe_count > 1:
                score += 5

    return {
        "score": score,
        "tolerance_f": tolerance_f,
        "has_second_oven": has_second_oven,
        "oven_heavy_recipe_count": oven_heavy_recipe_count,
        "temperatures_by_recipe": temperatures_by_recipe,
        "incompatible_pairs": incompatible_pairs,
        "missing_temperature_recipes": missing_temperature_recipes,
    }




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
        provenance=RecipeProvenance(
            kind="library_cookbook",
            source_label=selection.book_title,
            cookbook_id=str(selection.book_id),
        ),
    )


def build_cookbook_raw_recipes(concept: DinnerConcept) -> list[RawRecipe]:
    if concept.concept_source != "cookbook":
        return []
    return [_build_cookbook_raw_recipe(selection, concept.guest_count) for selection in concept.selected_recipes]


# ── Authored-mode deterministic compilation ─────────────────────────────────


def _format_authored_selection(selection: SelectedAuthoredRecipe | PlannerLibraryAuthoredRecipeAnchor) -> str:
    return f"{selection.title!r} ({selection.recipe_id})"


async def _load_authored_recipe_record(
    selection: SelectedAuthoredRecipe | PlannerLibraryAuthoredRecipeAnchor,
) -> AuthoredRecipeRecord:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_local = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session_local() as db:
            record = await db.get(AuthoredRecipeRecord, selection.recipe_id)
            if record is None:
                raise ValueError(f"Selected authored recipe {_format_authored_selection(selection)} was not found")
            return record
    finally:
        await engine.dispose()


async def _load_cookbook_authored_recipe_records(target: PlannerLibraryCookbookTarget) -> list[AuthoredRecipeRecord]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_local = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session_local() as db:
            stmt = (
                select(AuthoredRecipeRecord)
                .where(AuthoredRecipeRecord.cookbook_id == target.cookbook_id)
                .order_by(AuthoredRecipeRecord.updated_at.desc(), AuthoredRecipeRecord.title.asc())
            )
            return list((await db.execute(stmt)).scalars().all())
    finally:
        await engine.dispose()


async def _compile_authored_raw_recipe_from_record(
    selection: SelectedAuthoredRecipe | PlannerLibraryAuthoredRecipeAnchor,
    record: AuthoredRecipeRecord,
) -> RawRecipe:
    payload = dict(record.authored_payload or {})
    payload.setdefault("user_id", record.user_id)
    payload.setdefault("cookbook_id", record.cookbook_id)

    try:
        authored = AuthoredRecipeCreate.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"Selected authored recipe {_format_authored_selection(selection)} could not compile into a scheduling input: {exc}"
        ) from exc

    raw_recipe = authored.compile_raw_recipe()
    raw_recipe.provenance = RecipeProvenance(
        kind="library_authored",
        source_label=record.title,
        recipe_id=str(record.recipe_id),
        cookbook_id=str(record.cookbook_id) if record.cookbook_id else None,
    )
    if raw_recipe.name != selection.title:
        logger.warning(
            "Authored selection title drift detected for recipe %s: session title=%r db title=%r compiled title=%r",
            selection.recipe_id,
            selection.title,
            record.title,
            raw_recipe.name,
        )

    return raw_recipe


async def _compile_authored_raw_recipe(
    selection: SelectedAuthoredRecipe | PlannerLibraryAuthoredRecipeAnchor,
) -> RawRecipe:
    record = await _load_authored_recipe_record(selection)
    return await _compile_authored_raw_recipe_from_record(selection, record)


async def _build_authored_raw_recipe(concept: DinnerConcept) -> RawRecipe:
    if concept.selected_authored_recipe is None:
        raise ValueError("selected_authored_recipe is required when concept_source is 'authored'")
    return await _compile_authored_raw_recipe(concept.selected_authored_recipe)


async def _build_planner_authored_anchor_raw_recipe(concept: DinnerConcept) -> RawRecipe:
    if concept.planner_authored_recipe_anchor is None:
        raise ValueError("planner_authored_recipe_anchor is required when concept_source is 'planner_authored_anchor'")
    return await _compile_authored_raw_recipe(concept.planner_authored_recipe_anchor)


async def build_authored_raw_recipes(concept: DinnerConcept) -> list[RawRecipe]:
    if concept.concept_source != "authored":
        return []
    return [await _build_authored_raw_recipe(concept)]


async def build_planner_authored_anchor_raw_recipes(concept: DinnerConcept) -> list[RawRecipe]:
    if concept.concept_source != "planner_authored_anchor":
        return []
    return [await _build_planner_authored_anchor_raw_recipe(concept)]


async def build_planner_cookbook_target_raw_recipes(concept: DinnerConcept) -> list[RawRecipe]:
    if concept.concept_source != "planner_cookbook_target":
        return []

    target = concept.planner_cookbook_target
    if target is None:
        raise ValueError("planner_cookbook_target is required when concept_source is 'planner_cookbook_target'")

    records = await _load_cookbook_authored_recipe_records(target)
    if not records:
        raise ValueError(
            f"Planner cookbook target {target.name!r} ({target.cookbook_id}) has no authored recipes available for runtime seeding"
        )

    compiled_recipes: list[RawRecipe] = []
    compile_errors: list[str] = []
    for record in records:
        selection = PlannerLibraryAuthoredRecipeAnchor(recipe_id=record.recipe_id, title=record.title)
        try:
            compiled_recipes.append(await _compile_authored_raw_recipe_from_record(selection, record))
        except ValueError as exc:
            compile_errors.append(str(exc))

    if not compiled_recipes:
        raise ValueError(
            f"Planner cookbook target {target.name!r} ({target.cookbook_id}) did not contain any authored recipes that could compile into scheduling inputs. "
            + "; ".join(compile_errors)
        )

    return compiled_recipes


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


async def _invoke_recipe_generation(
    *,
    system_prompt: str,
    human_prompt: str,
) -> tuple[RecipeGenerationOutput, dict]:
    """Run the structured LLM recipe generation path and return output + token usage."""
    llm = _create_llm()
    chain = llm.with_structured_output(RecipeGenerationOutput)

    @llm_retry
    async def _invoke_llm():
        return await chain.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt),
            ]
        )

    result = await _invoke_llm()
    usage = extract_token_usage(result, "recipe_generator")
    return result, usage


async def _invoke_recipe_generation_candidates(
    *,
    system_prompt: str,
    human_prompt: str,
    candidate_count: int,
) -> tuple[list[RecipeGenerationOutput], list[dict]]:
    """Generate a bounded set of candidate menus while preserving per-call token usage."""
    candidate_results: list[RecipeGenerationOutput] = []
    usages: list[dict] = []

    for _ in range(candidate_count):
        result, usage = await _invoke_recipe_generation(system_prompt=system_prompt, human_prompt=human_prompt)
        candidate_results.append(result)
        usages.append(usage)

    return candidate_results, usages



def _select_best_recipe_generation_candidate(
    *,
    candidate_results: list[RecipeGenerationOutput],
    anchor_recipes: list[RawRecipe] | None,
    has_second_oven: bool,
) -> tuple[RecipeGenerationOutput, dict]:
    if not candidate_results:
        raise ValueError("Recipe generation did not return any candidate menus")

    seeded_anchor_recipes = list(anchor_recipes or [])
    scored_candidates: list[tuple[int, int, RecipeGenerationOutput, dict]] = []
    for index, candidate in enumerate(candidate_results):
        recipes = [*seeded_anchor_recipes, *candidate.recipes]
        score_details = _score_menu_oven_compatibility(recipes=recipes, has_second_oven=has_second_oven)
        scored_candidates.append((score_details["score"], index, candidate, score_details))

    best_score, _, best_candidate, best_details = min(scored_candidates, key=lambda item: (item[0], item[1]))
    logger.info(
        "Selected generator candidate %d/%d with oven score=%d incompatible_pairs=%s oven_heavy=%d",
        next(index for _, index, candidate, _ in scored_candidates if candidate is best_candidate) + 1,
        len(scored_candidates),
        best_score,
        best_details["incompatible_pairs"],
        best_details["oven_heavy_recipe_count"],
    )
    return best_candidate, best_details


async def _generate_ranked_recipe_candidates(
    *,
    system_prompt: str,
    human_prompt: str,
    kitchen_config: dict,
    anchor_recipes: list[RawRecipe] | None = None,
) -> tuple[RecipeGenerationOutput, list[dict], dict]:
    candidate_results, usages = await _invoke_recipe_generation_candidates(
        system_prompt=system_prompt,
        human_prompt=human_prompt,
        candidate_count=3,
    )
    best_candidate, score_details = _select_best_recipe_generation_candidate(
        candidate_results=candidate_results,
        anchor_recipes=anchor_recipes,
        has_second_oven=kitchen_config.get("has_second_oven", False),
    )
    return best_candidate, usages, score_details


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

        if concept.concept_source == "authored":
            authored_recipes = await build_authored_raw_recipes(concept)
            selected = concept.selected_authored_recipe
            logger.info(
                "Seeded %d authored recipe from persisted selection %s",
                len(authored_recipes),
                selected.recipe_id if selected else "unknown",
            )
            return {
                "raw_recipes": [recipe.model_dump(mode="json") for recipe in authored_recipes],
            }

        recipe_count = _derive_recipe_count(concept.meal_type, concept.occasion)

        if concept.concept_source == "planner_authored_anchor":
            anchor_recipes = await build_planner_authored_anchor_raw_recipes(concept)
            anchor_recipe = anchor_recipes[0]
            complement_count = max(0, recipe_count - len(anchor_recipes))

            if complement_count == 0:
                logger.info(
                    "Seeded planner-authored anchor %r with no complementary generation required",
                    anchor_recipe.name,
                )
                return {
                    "raw_recipes": [recipe.model_dump(mode="json") for recipe in anchor_recipes],
                }

            system_prompt = _build_mixed_origin_system_prompt(
                concept=concept,
                kitchen_config=kitchen_config,
                equipment=equipment,
                anchor_recipe=anchor_recipe,
                complement_count=complement_count,
            )
            human_prompt = _build_mixed_origin_human_prompt(complement_count, anchor_recipe)

            logger.info(
                "Generating %d complementary recipe candidates around planner-authored anchor %r",
                complement_count,
                anchor_recipe.name,
            )
            result, usages, _score_details = await _generate_ranked_recipe_candidates(
                system_prompt=system_prompt,
                human_prompt=human_prompt,
                kitchen_config=kitchen_config,
                anchor_recipes=anchor_recipes,
            )
            all_recipes = [anchor_recipe, *result.recipes]
            logger.info(
                "Seeded planner-authored anchor %r and selected %d complementary recipes: %s",
                anchor_recipe.name,
                len(result.recipes),
                [r.name for r in result.recipes],
            )
            return {
                "raw_recipes": [recipe.model_dump(mode="json") for recipe in all_recipes],
                "token_usage": usages,
            }

        if concept.concept_source == "planner_cookbook_target":
            target = concept.planner_cookbook_target
            cookbook_recipes = await build_planner_cookbook_target_raw_recipes(concept)
            cookbook_mode = target.mode if target is not None else PlannerLibraryCookbookPlanningMode.STRICT

            if cookbook_mode == PlannerLibraryCookbookPlanningMode.STRICT:
                seeded_recipes = cookbook_recipes[:recipe_count]
                logger.info(
                    "Seeded %d planner cookbook recipes from %r in strict mode",
                    len(seeded_recipes),
                    target.name if target else "unknown cookbook",
                )
                return {
                    "raw_recipes": [recipe.model_dump(mode="json") for recipe in seeded_recipes],
                }

            anchor_recipe = cookbook_recipes[0]
            seeded_recipes = [anchor_recipe]
            complement_count = max(0, recipe_count - len(seeded_recipes))
            if complement_count == 0:
                logger.info(
                    "Seeded planner cookbook anchor %r from %r with no complementary generation required",
                    anchor_recipe.name,
                    target.name if target else "unknown cookbook",
                )
                return {
                    "raw_recipes": [recipe.model_dump(mode="json") for recipe in seeded_recipes],
                }

            system_prompt = _build_mixed_origin_system_prompt(
                concept=concept,
                kitchen_config=kitchen_config,
                equipment=equipment,
                anchor_recipe=anchor_recipe,
                complement_count=complement_count,
            )
            human_prompt = _build_mixed_origin_human_prompt(complement_count, anchor_recipe)

            logger.info(
                "Seeded planner cookbook anchor %r from %r and generating %d complementary candidates",
                anchor_recipe.name,
                target.name if target else "unknown cookbook",
                complement_count,
            )
            result, usages, _score_details = await _generate_ranked_recipe_candidates(
                system_prompt=system_prompt,
                human_prompt=human_prompt,
                kitchen_config=kitchen_config,
                anchor_recipes=seeded_recipes,
            )
            all_recipes = [*seeded_recipes, *result.recipes]
            logger.info(
                "Seeded planner cookbook target %r and selected %d complementary recipes: %s",
                target.name if target else "unknown cookbook",
                len(result.recipes),
                [r.name for r in result.recipes],
            )
            return {
                "raw_recipes": [recipe.model_dump(mode="json") for recipe in all_recipes],
                "token_usage": usages,
            }

        # Derive recipe count from concept
        system_prompt = _build_system_prompt(concept, kitchen_config, equipment, recipe_count)
        human_prompt = _build_human_prompt(recipe_count, concept)

        logger.info("Generating %d recipe candidates for %s %s", recipe_count, concept.occasion.value, concept.meal_type.value)
        result, usages, _score_details = await _generate_ranked_recipe_candidates(
            system_prompt=system_prompt,
            human_prompt=human_prompt,
            kitchen_config=kitchen_config,
        )

        logger.info("Selected %d generated recipes: %s", len(result.recipes), [r.name for r in result.recipes])
        # Return raw_recipes as dicts (replace semantics)
        return {
            "raw_recipes": [r.model_dump() for r in result.recipes],
            "token_usage": usages,
        }

    except Exception as exc:
        # Preserve deterministic parse failures as explicit validation failures.
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
