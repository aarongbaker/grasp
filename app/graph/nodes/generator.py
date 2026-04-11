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
from typing import cast

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError
from sqlalchemy import asc, desc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import select

from app.api.routes.catalog import load_catalog_runtime_seed_recipes
from app.core.llm import extract_token_usage, is_timeout_error, llm_retry
from app.core.settings import get_settings
from app.models.authored_recipe import AuthoredRecipeCreate, AuthoredRecipeRecord
from app.models.enums import ErrorType, MealType, Occasion
from app.models.errors import NodeError
from app.models.pipeline import (
    DinnerConcept,
    GRASPState,
    GenerationAttemptRecord,
    GenerationRetryReason,
    PlannerLibraryAuthoredRecipeAnchor,
    PlannerLibraryCookbookPlanningMode,
    PlannerLibraryCookbookTarget,
    SelectedAuthoredRecipe,
    SelectedCookbookRecipe,
)
from app.models.recipe import Ingredient, RawRecipe, RecipeProvenance

logger = logging.getLogger(__name__)

# ── Structured output wrapper ────────────────────────────────────────────────


class RecipeGenerationOutput(BaseModel):
    """Wrapper for LangChain structured output. Claude returns this shape."""

    # LangChain's with_structured_output() forces Claude to produce valid JSON
    # matching this Pydantic model. The wrapper layer exists because Claude
    # returns a list, but structured output needs a top-level object — so we
    # wrap the list in a single field and unwrap downstream.
    recipes: list[RawRecipe]


# ── Recipe count derivation ──────────────────────────────────────────────────

# Static lookup table: (MealType, Occasion) → how many recipes to generate.
# The counts encode deliberate culinary judgment — a casual breakfast is one
# dish, a tasting-menu dinner is five courses. Maintained here rather than in
# a DB because these are editorial constants, not user-configurable data. If
# the (meal_type, occasion) pair isn't in the map, DEFAULT_RECIPE_COUNT is
# used as a safe fallback. The user can override either value via dish_count.
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
    (MealType.APPETIZERS, Occasion.MEAL_PREP): 4,
    (MealType.SNACKS, Occasion.MEAL_PREP): 4,
    (MealType.DESSERT, Occasion.MEAL_PREP): 3,
    (MealType.MEAL_PREP, Occasion.MEAL_PREP): 4,
}

# Fallback count used when (meal_type, occasion) isn't mapped above. Three is
# the smallest count that yields a meaningful multi-course feel — starter,
# main, dessert — while still being schedulable with a single oven.
DEFAULT_RECIPE_COUNT = 3


def _derive_recipe_count(meal_type: MealType, occasion: Occasion) -> int:
    """Lookup target recipe count from meal_type + occasion."""
    return RECIPE_COUNT_MAP.get((meal_type, occasion), DEFAULT_RECIPE_COUNT)


def _resolve_recipe_count(concept: DinnerConcept) -> int:
    """Prefer explicit user dish count; fall back to meal/occasion defaults."""
    # dish_count is an optional override set by the user at session creation time
    # (e.g. "I want 4 courses for my dinner party"). When present it takes precedence
    # over the opinionated RECIPE_COUNT_MAP defaults, giving users direct control
    # without requiring them to understand the meal_type/occasion taxonomy.
    return concept.dish_count or _derive_recipe_count(concept.meal_type, concept.occasion)


# ── Prompt builders ──────────────────────────────────────────────────────────


def _format_dietary_restrictions(restrictions: list[str]) -> str:
    # Returns "None specified." rather than an empty string so the prompt never
    # has a blank section — blank sections can cause Claude to treat the field
    # as absent and silently drop the constraint.
    if not restrictions:
        return "None specified."
    return "\n".join(f"- {r}" for r in restrictions)


def _format_equipment(equipment: list[dict]) -> str:
    # equipment dicts come from the user's KitchenEquipment rows serialised as
    # JSON in GRASPState. We surface unlocks_techniques so Claude can choose
    # more advanced methods (e.g. sous vide, smoking) when the hardware is present.
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
    # Pre-format the variable sections so the f-string below stays readable.
    restrictions = _format_dietary_restrictions(concept.dietary_restrictions)
    equip_text = _format_equipment(equipment)

    # Kitchen config comes from the KitchenConfig row, defaulting safely if
    # the session was created before these fields existed.
    max_burners = kitchen_config.get("max_burners", 4)
    max_oven_racks = kitchen_config.get("max_oven_racks", 2)
    has_second_oven = kitchen_config.get("has_second_oven", False)

    # Oven-compatibility guidance is injected as a numbered rule — the wording
    # differs significantly between single- and dual-oven kitchens.
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
11. {oven_guidance}
12. Assign a 'course' value to every recipe using one of: appetizer, soup, salad, entree, side, dessert, other. Every multi-course menu must include exactly one recipe with course='entree'.'''


def _build_mixed_origin_system_prompt(
    concept: DinnerConcept,
    kitchen_config: dict,
    equipment: list[dict],
    anchor_recipe: RawRecipe,
    complement_count: int,
) -> str:
    # This prompt is used for planner_authored_anchor and planner_cookbook_target
    # (biased mode) — the anchor is already committed, so we provide its full
    # summary to Claude as a fixed constraint. Crucially, guideline 2 explicitly
    # forbids regenerating the anchor: without it, Claude occasionally "helpfully"
    # rewrites the dish the user specifically chose.
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
11. {oven_guidance}
12. Assign a 'course' value to every generated recipe using one of: appetizer, soup, salad, entree, side, dessert, other. The anchor recipe above is the entree — generated complements should use appropriate course values.'''


def _build_oven_compatibility_prompt_guidance(*, has_second_oven: bool) -> str:
    # Single-oven and dual-oven kitchens need different constraints baked into the
    # generation prompt. In a single-oven kitchen, overlapping oven-heavy dishes at
    # incompatible temperatures is a hard scheduling failure — the DAG merger will
    # catch it and potentially retry generation. Surfacing this constraint here at
    # generation time gives Claude the best chance to avoid the conflict before it
    # reaches the scheduler. The dual-oven path is deliberately lenient; the only
    # guidance is to "prefer" compatible workloads rather than require them.
    if has_second_oven:
        return (
            "Prefer menus with naturally compatible oven workloads, but a second oven means "
            "parallel dishes may use meaningfully different temperatures when needed."
        )

    # Single-oven: the entree's oven temperature is the load-bearing anchor.
    # The 15°F tolerance here matches the same constant used in
    # _score_menu_oven_compatibility and in the DAG merger conflict classifier —
    # they must stay in sync or the prompt guidance will contradict the scheduler.
    return (
        "For single-oven kitchens, generate only menus whose oven-temperature windows can actually be executed on one oven without "
        "temperature-conflict overlap. Treat oven temperatures within about 15°F of each other as compatible, avoid pairing overlapping "
        "long low braises with high-heat bakes or desserts unless timing can be serialized cleanly, and if tension remains, prefer menus with "
        "only one oven-heavy dish plus stovetop/passive complements. If a requested menu shape would otherwise force overlapping incompatible "
        "oven temperatures, choose different dishes or cooking methods instead of returning an impossible plan. "
        "The entree's oven temperature anchors the entire menu — other dishes that use the oven while the entree cooks must use a compatible "
        "temperature (within 15°F), or they must complete their oven work before the entree goes in or after it comes out. "
        "Prefer stovetop, passive, or hands-based techniques for non-entree dishes when oven temperature compatibility is uncertain."
    )


def _build_human_prompt(recipe_count: int, concept: DinnerConcept) -> str:
    # The human message re-states the recipe count and concept so Claude has it
    # in both the system context and the conversational turn — reducing the chance
    # of count drift when the model's attention window is under pressure.
    return (
        f"Generate exactly {recipe_count} recipes for this menu. "
        f"The menu should satisfy the concept {concept.free_text!r} and return only the structured recipe payload."
    )


def _build_mixed_origin_human_prompt(complement_count: int, anchor_recipe: RawRecipe) -> str:
    # Echoes the anchor name in the human turn to reinforce the "don't regenerate"
    # constraint from the system prompt — belt-and-suspenders against anchor drift.
    return (
        f"Generate {complement_count} complementary recipes around the anchored dish {anchor_recipe.name!r}. "
        "Do not repeat the anchor recipe and return only the structured recipe payload."
    )


def _format_retry_conflict_details(retry_reason: GenerationRetryReason) -> str:
    # Formats the rich conflict context from the DAG merger into a structured
    # section that gets embedded directly into the retry system prompt. The
    # scheduler-provided details (temperature_gap_f, blocking_recipe_names,
    # suggested_actions) give Claude the exact nature of what went wrong so it
    # can make targeted changes rather than regenerating blindly.
    summary = retry_reason.summary
    lines = [
        f"- Triggering node: {retry_reason.node_name}",
        f"- Attempt that failed: {retry_reason.attempt}",
        f"- Scheduler classification: {summary.classification}",
        f"- Single-oven kitchen: {'Yes' if not summary.has_second_oven else 'No'}",
        f"- Allowed oven temperature tolerance: {summary.tolerance_f}°F",
    ]

    # Only include optional fields if they're populated — avoids printing "None"
    # values into the prompt, which wastes tokens and can confuse the model.
    if summary.temperature_gap_f is not None:
        lines.append(f"- Reported conflicting temperature gap: {summary.temperature_gap_f}°F")
    if summary.blocking_recipe_names:
        lines.append("- Blocking recipe names: " + ", ".join(summary.blocking_recipe_names))
    if summary.affected_step_ids:
        lines.append("- Affected scheduler step ids: " + ", ".join(summary.affected_step_ids))
    if summary.remediation.suggested_actions:
        lines.append("- Scheduler suggested actions: " + "; ".join(summary.remediation.suggested_actions))
    if summary.remediation.notes:
        lines.append(f"- Scheduler notes: {summary.remediation.notes}")

    lines.append(f"- Conflict detail: {retry_reason.detail}")
    return "\n".join(lines)


def _build_retry_system_prompt(
    concept: DinnerConcept,
    kitchen_config: dict,
    equipment: list[dict],
    recipe_count: int,
    retry_reason: GenerationRetryReason,
) -> str:
    # This prompt is used exclusively on retry attempts — when dag_merger
    # detected a scheduling conflict it could not resolve and routed the state
    # back to generator with a populated generation_retry_reason. The key
    # difference from the standard prompt is SCHEDULER CONFLICT CONTEXT, which
    # embeds the authoritative machine-generated conflict description so Claude
    # understands exactly which dishes conflicted and why.
    restrictions = _format_dietary_restrictions(concept.dietary_restrictions)
    equip_text = _format_equipment(equipment)
    retry_details = _format_retry_conflict_details(retry_reason)

    max_burners = kitchen_config.get("max_burners", 4)
    max_oven_racks = kitchen_config.get("max_oven_racks", 2)
    has_second_oven = kitchen_config.get("has_second_oven", False)
    oven_guidance = _build_oven_compatibility_prompt_guidance(has_second_oven=has_second_oven)

    # The corrective rule differs by oven configuration — for single-oven the
    # entree temperature is the immovable anchor, so conflicting dishes must
    # change cooking method. For dual-oven the guidance is softer.
    single_oven_retry_rule = (
        "This is a corrective retry for a one-oven conflict. The entree's oven temperature is the fixed anchor for the entire menu. "
        "All other dishes must either: (a) use a temperature within 15°F of the entree's oven temperature, "
        "(b) complete their oven work before the entree goes in, or (c) switch to stovetop or passive methods instead. "
        "Do not keep the same temperature conflict shape — if the conflicting dish cannot match the entree's oven temperature, change its cooking method."
        if not has_second_oven
        else "This is a corrective retry informed by scheduler conflict context. Use the conflict details to avoid repeating the same failure shape, but parallel oven dishes may use meaningfully different temperatures because a second oven is available."
    )

    return f'''You are GRASP, an expert chef assistant performing corrective regeneration after the scheduler rejected the previous menu.
Your task is to produce a NEW feasible multi-course menu, not to restate the failed one.

## ORIGINAL DINNER CONCEPT
"{concept.free_text}"

## MENU PARAMETERS
- Meal type: {concept.meal_type.value}
- Occasion: {concept.occasion.value}
- Guest count: {concept.guest_count}
- Number of courses to generate: {recipe_count}
- Retry attempt: {retry_reason.attempt + 1}

## DIETARY RESTRICTIONS
{restrictions}

## KITCHEN CONSTRAINTS
- Stovetop burners available: {max_burners}
- Oven racks available: {max_oven_racks}
- Second oven: {"Yes" if has_second_oven else "No"}

## AVAILABLE EQUIPMENT
{equip_text}

## SCHEDULER CONFLICT CONTEXT (AUTHORITATIVE)
{retry_details}

## CORRECTIVE REQUIREMENTS
1. Generate exactly {recipe_count} recipes forming a balanced, cohesive {concept.occasion.value} {concept.meal_type.value} menu.
2. Scale all ingredient quantities for {concept.guest_count} servings.
3. Write clear, detailed steps with temperatures (Celsius), times, and visual doneness cues.
4. STRICTLY respect all dietary restrictions. Never include restricted ingredients or derivatives.
5. Treat the scheduler conflict context above as a hard constraint for this retry.
6. Do NOT reproduce the same beyond-tolerance overlap shape, blocking recipe combination, or oven-temperature conflict described above.
7. If the prior concept implicitly suggested incompatible dishes, preserve the spirit of the meal while changing dishes or cooking methods enough to become feasible.
8. Design recipes that work within the kitchen's burner and oven rack limits.
9. Include cuisine attribution for each recipe.
10. Provide realistic estimated_total_minutes for each recipe (prep through plating).
11. Each recipe must have at least 3 steps. Steps should be detailed enough for an intermediate cook.
12. {oven_guidance}
13. {single_oven_retry_rule}
14. Assign a 'course' value to every recipe using one of: appetizer, soup, salad, entree, side, dessert, other. Include exactly one recipe with course='entree'.'''


def _build_retry_human_prompt(recipe_count: int, concept: DinnerConcept, retry_reason: GenerationRetryReason) -> str:
    # The human message for a retry call names the blocking recipes explicitly so
    # they appear in the conversational turn, not just buried in system context —
    # Claude tends to honour constraints more reliably when they appear in both.
    summary = retry_reason.summary
    blocking = ", ".join(summary.blocking_recipe_names) if summary.blocking_recipe_names else "the conflicting dishes"
    return (
        f"Regenerate exactly {recipe_count} recipes for the concept {concept.free_text!r}. "
        f"The previous menu failed because {blocking} created an irreconcilable oven conflict in a "
        f"{'single-oven' if not summary.has_second_oven else 'multi-oven'} kitchen. "
        "Return only the structured recipe payload, and make sure the new menu does not repeat that conflict pattern."
    )


def _extract_oven_step_temperatures(recipe: RawRecipe) -> list[int]:
    # Lightweight heuristic temperature extractor — runs over step text to find
    # any temperatures associated with oven operations. Used by the pre-flight
    # oven-compatibility scorer to catch obvious conflicts before they hit the
    # DAG merger. Not intended to be exhaustive; the DAG merger has a more
    # authoritative conflict checker that operates on the scheduled timeline.
    temperatures: list[int] = []
    for step in recipe.steps:
        lower_step = step.lower()
        # Only inspect steps that mention oven-related operations — stovetop
        # temperatures are irrelevant to oven-conflict detection.
        if (
            "oven" not in lower_step
            and "bake" not in lower_step
            and "roast" not in lower_step
            and "broil" not in lower_step
        ):
            continue

        # Try Fahrenheit first (e.g. "375°F", "375 F")
        fahrenheit_match = re.search(r"(\d{3})\s*°?\s*f", lower_step)
        if fahrenheit_match:
            temperatures.append(int(fahrenheit_match.group(1)))
            continue

        # Celsius fallback — convert to Fahrenheit for unified comparison
        celsius_match = re.search(r"(\d{2,3})\s*°?\s*c", lower_step)
        if celsius_match:
            celsius = int(celsius_match.group(1))
            temperatures.append(round((celsius * 9 / 5) + 32))
            continue

        # Last-resort: map vague heat descriptors to representative Fahrenheit
        # values so "hot oven" is still comparable to "450°F" rather than
        # being silently ignored and missing the conflict.
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
    # Scores a candidate menu for oven compatibility BEFORE accepting it.
    # This is the generator-side pre-flight check — a lower score is better.
    # When generating 3 candidates, we pick the lowest-scoring one to maximise
    # the chance that the selected menu passes the DAG merger's stricter check
    # without needing a retry generation cycle.
    #
    # Scoring rationale:
    #   +20 per extra oven-heavy recipe (beyond the first) in a single-oven kitchen
    #   +50 per incompatible recipe pair in a single-oven kitchen (hard conflict)
    #   +5  per compatible-but-crowded pair in a single-oven kitchen (soft tension)
    #   0   for everything in a dual-oven kitchen (not meaningfully constrained here)
    tolerance_f = 15  # must match the tolerance used in dag_merger's conflict classifier
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
            # Recipe uses no oven (or uses one but doesn't state a temperature)
            # — track separately so callers can warn when critical info is absent.
            missing_temperature_recipes.append(recipe.name)

    if has_second_oven:
        # Dual-oven kitchen — we still compute pairs below for logging, but the
        # score stays zero because parallel oven loads are allowed.
        score = 0
    else:
        # Each additional oven-heavy dish in a single-oven kitchen adds scheduling
        # pressure — even if temperatures are compatible, serialising oven use
        # tightens the critical path.
        score = max(0, oven_heavy_recipe_count - 1) * 20

    # Check every pair of oven-heavy recipes for temperature compatibility.
    # O(n²) over recipes, which is fine because n ≤ 5 in practice.
    recipe_items = list(temperatures_by_recipe.items())
    for index, (recipe_a, temps_a) in enumerate(recipe_items):
        for recipe_b, temps_b in recipe_items[index + 1 :]:
            if not temps_a or not temps_b:
                # At least one recipe has no detected oven temperature — can't
                # assess compatibility, so we skip rather than penalise.
                continue

            # Use the minimum cross-product gap — if any temperature pair is
            # within tolerance, we consider the recipes compatible.
            min_gap = min(abs(temp_a - temp_b) for temp_a in temps_a for temp_b in temps_b)
            if min_gap > tolerance_f:
                incompatible_pairs.append((recipe_a, recipe_b, temps_a[0], temps_b[0]))
                if not has_second_oven:
                    # Hard conflict — DAG merger will likely reject this menu
                    score += 50
            elif not has_second_oven and oven_heavy_recipe_count > 1:
                # Compatible but still crowding the single oven
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
    # Cookbook chunks are often stored as raw Markdown. This strips leading #
    # characters so section headers don't pollute recipe names or step lists.
    return re.sub(r"^#{1,6}\s*", "", line).strip()


def _normalise_lines(text: str) -> list[str]:
    # Drop blank lines and leading/trailing whitespace to produce a clean list
    # of meaningful lines that the extraction functions can scan sequentially.
    return [line.strip() for line in text.splitlines() if line.strip()]


def _extract_recipe_name(selection: SelectedCookbookRecipe) -> str:
    # Heuristic: the recipe name is the first non-header, non-section-title line
    # in the chunk. Cookbook chunks are retrieved from Pinecone and their exact
    # structure depends on how the book was chunked during ingestion — this
    # handles the common case of "# Recipe Name\n## Ingredients\n..." layout.
    for line in _normalise_lines(selection.text):
        cleaned = _strip_markdown_heading_prefix(line)
        # Skip canonical section headers — they're structure, not content
        if cleaned and not cleaned.lower().startswith(("ingredients", "method", "directions", "steps")):
            return cleaned[:200]
    # If no suitable line is found, fall back to chapter or page reference so
    # the recipe is always identifiable, even if not pretty.
    fallback = selection.chapter.strip() or f"Cookbook recipe p.{selection.page_number}"
    return fallback[:200]


def _extract_ingredient_lines(lines: list[str]) -> list[str]:
    # State machine that extracts the ingredient block from a normalised line
    # list. Starts scanning on the "Ingredients" header and stops at any
    # recognised method/step header. Handles cookbook chunks where the sections
    # appear in canonical order (ingredients first, then method).
    ingredients: list[str] = []
    in_ingredients = False
    for line in lines:
        lowered = _strip_markdown_heading_prefix(line).lower().rstrip(":")
        if lowered in {"ingredients", "for the ingredients"}:
            in_ingredients = True
            continue
        # Any step-section header terminates the ingredient block
        if lowered in {"method", "directions", "steps", "preparation"}:
            break
        if in_ingredients:
            ingredients.append(line)
    return ingredients


def _extract_step_lines(lines: list[str]) -> list[str]:
    # State machine for the method/steps block — symmetric with
    # _extract_ingredient_lines above. Strips leading list markers (numbers,
    # bullets) so each step is clean prose that can be stored directly in
    # RecipeStep.description.
    steps: list[str] = []
    in_steps = False
    for line in lines:
        cleaned = _strip_markdown_heading_prefix(line)
        lowered = cleaned.lower().rstrip(":")
        if lowered in {"method", "directions", "steps", "preparation"}:
            in_steps = True
            continue
        if in_steps:
            # Remove "1." / "2)" / "-" / "•" style list prefixes
            steps.append(re.sub(r"^(?:\d+[\.)]|[-*•])\s*", "", cleaned).strip())
    return [step for step in steps if step]


def _parse_ingredient(line: str) -> Ingredient:
    # Parses a single ingredient line from a cookbook chunk into a structured
    # Ingredient(name, quantity) pair. Cookbook formatting is wildly inconsistent
    # across different books and chunking strategies, so the parser tries several
    # patterns in decreasing specificity before falling back to "as needed".
    cleaned = re.sub(r"^(?:[-*•])\s*", "", line).strip()
    if not cleaned:
        raise ValueError("Empty ingredient line")

    # "200g butter – for the crust" style (em-dash separator)
    if " – " in cleaned:
        quantity, name = cleaned.split(" – ", 1)
    # "200g butter - for the crust" style (hyphen separator)
    elif " - " in cleaned:
        quantity, name = cleaned.split(" - ", 1)
    else:
        parts = cleaned.split()
        # "200 g butter" — amount + unit + name (3+ tokens, first is numeric)
        if len(parts) >= 3 and any(ch.isdigit() for ch in parts[0]):
            quantity = " ".join(parts[:2])
            name = " ".join(parts[2:])
        # "2 eggs" — bare count + name
        elif len(parts) >= 2 and any(ch.isdigit() for ch in parts[0]):
            quantity = parts[0]
            name = " ".join(parts[1:])
        else:
            # No numeric prefix found — treat the whole line as a name and
            # use "as needed" to avoid losing the ingredient entirely.
            quantity = "as needed"
            name = cleaned

    return Ingredient(name=name.strip()[:200], quantity=quantity.strip()[:100] or "as needed")


def _estimate_minutes(step_count: int) -> int:
    # Rough heuristic for cookbook chunks that don't state a total time.
    # 15 minutes per step is conservative but avoids DAG merger under-scheduling.
    # The minimum of 15 ensures single-step chunks (which shouldn't exist but
    # might slip through validation) still get a non-zero duration.
    return max(15, step_count * 15)


def _build_cookbook_raw_recipe(selection: SelectedCookbookRecipe, guest_count: int) -> RawRecipe:
    # Converts a Pinecone-retrieved cookbook chunk (SelectedCookbookRecipe) into
    # a RawRecipe that can flow through the full enricher → validator → DAG
    # pipeline. This is the deterministic conversion path — no LLM is called.
    #
    # Failure modes:
    #   - Empty chunk text → ValueError (shouldn't occur post-search but defensive)
    #   - < 3 steps extracted → ValueError (DAG builder requires at least 3 for
    #     meaningful parallelism analysis; fewer steps signal a malformed chunk)
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
        # If the chunk contains no parseable ingredients (e.g. a method-only
        # chunk), insert a placeholder so the schema stays valid and the enricher
        # can attempt RAG-backed ingredient expansion later.
        ingredients = [Ingredient(name="See cookbook source text", quantity="as needed")]

    # Cookbook text doesn't reliably state original yield, so use a
    # conservative default rather than claiming guest_count (which would
    # imply ingredients are already scaled when they aren't).
    cookbook_default_servings = 4

    return RawRecipe(
        name=_extract_recipe_name(selection),
        description=(
            f"Cookbook-selected recipe from {selection.book_title}"
            + (f", chapter {selection.chapter}" if selection.chapter else "")
            + (f", page {selection.page_number}" if selection.page_number else "")
            + f". Original yield ~{cookbook_default_servings} servings; scale to {guest_count} guests."
        ),
        servings=cookbook_default_servings,
        # Carry over the course tag if it was set during cookbook ingestion
        # (e.g. the user tagged the chapter as "entree" in the library UI).
        course=getattr(selection, "course", None),
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
    # Public entry point for the cookbook path — guards against wrong concept_source
    # so callers can call this unconditionally and receive an empty list when the
    # session isn't in cookbook mode (simplifies the dispatch logic in the node).
    if concept.concept_source != "cookbook":
        return []
    return [_build_cookbook_raw_recipe(selection, concept.guest_count) for selection in concept.selected_recipes]


# ── Authored-mode deterministic compilation ─────────────────────────────────


def _format_authored_selection(selection: SelectedAuthoredRecipe | PlannerLibraryAuthoredRecipeAnchor) -> str:
    # Produces a human-readable identifier for log messages and error strings.
    # Includes both title and ID because title alone isn't unique across users.
    return f"{selection.title!r} ({selection.recipe_id})"


async def _load_authored_recipe_record(
    selection: SelectedAuthoredRecipe | PlannerLibraryAuthoredRecipeAnchor,
) -> AuthoredRecipeRecord:
    # Opens a short-lived DB connection specifically for this lookup.
    # We do NOT reuse the application's connection pool here because the
    # LangGraph node runs in a background worker context where the FastAPI
    # request-scoped session is not available. Creating and immediately disposing
    # the engine keeps the connection count predictable and avoids leaks.
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session_local() as db:
            record = await db.get(AuthoredRecipeRecord, selection.recipe_id)
            if record is None:
                # This happens if the user deleted the recipe after creating
                # the session — the session snapshot holds the recipe_id but
                # the DB row is gone. Raise ValueError so the node catches it
                # as VALIDATION_FAILURE (not LLM_PARSE_FAILURE).
                raise ValueError(f"Selected authored recipe {_format_authored_selection(selection)} was not found")
            return record
    finally:
        # Always dispose — prevents stale connection pool objects after the
        # one-shot engine goes out of scope.
        await engine.dispose()


async def _load_cookbook_authored_recipe_records(target: PlannerLibraryCookbookTarget) -> list[AuthoredRecipeRecord]:
    # Loads ALL authored recipes belonging to a planner cookbook target, ordered
    # by recency (most recently updated first) as a tiebreak for consistent
    # selection when multiple recipes exist. This is used by the
    # planner_cookbook_target path to seed the full cookbook's recipe list, from
    # which the node then picks the top N by recipe_count.
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session_local() as db:
            stmt = (
                select(AuthoredRecipeRecord)
                .where(AuthoredRecipeRecord.cookbook_id == target.cookbook_id)
                # updated_at desc ensures the freshest recipes appear first.
                # title asc is a stable secondary sort for reproducibility when
                # timestamps are equal (e.g. bulk-imported cookbooks).
                .order_by(desc(AuthoredRecipeRecord.updated_at), asc(AuthoredRecipeRecord.title))  # type: ignore[arg-type]
            )
            return list((await db.execute(stmt)).scalars().all())
    finally:
        await engine.dispose()


async def _compile_authored_raw_recipe_from_record(
    selection: SelectedAuthoredRecipe | PlannerLibraryAuthoredRecipeAnchor,
    record: AuthoredRecipeRecord,
) -> RawRecipe:
    # Core compilation step: takes a raw DB record (authored_payload JSON blob)
    # and produces a structured RawRecipe via AuthoredRecipeCreate.compile_raw_recipe().
    # The authored_payload carries the user's hand-authored recipe in a flexible
    # JSON schema — validate_model handles schema evolution (older payloads may
    # be missing newer optional fields).
    payload = dict(record.authored_payload or {})
    # Inject user_id and cookbook_id into the payload if missing — they live on
    # the record top-level but may not be in the authored_payload blob itself
    # (authored_payload was designed as a user-facing recipe schema, not a DB row).
    payload.setdefault("user_id", record.user_id)
    payload.setdefault("cookbook_id", record.cookbook_id)

    try:
        authored = AuthoredRecipeCreate.model_validate(payload)
    except ValidationError as exc:
        # Payload is structurally invalid — surface as ValueError so the caller
        # can treat it as VALIDATION_FAILURE rather than a generic exception.
        raise ValueError(
            f"Selected authored recipe {_format_authored_selection(selection)} could not compile into a scheduling input: {exc}"
        ) from exc

    raw_recipe = authored.compile_raw_recipe()
    # Attach provenance so downstream nodes (enricher, renderer) know this recipe
    # came from the user's library, not from LLM generation — affects RAG strategy
    # and the "from library" badge in the UI.
    raw_recipe.provenance = RecipeProvenance(
        kind="library_authored",
        source_label=record.title,
        recipe_id=str(record.recipe_id),
        cookbook_id=str(record.cookbook_id) if record.cookbook_id else None,
    )
    # Title drift warning: the session was created with the recipe title at that
    # moment. If the user later renamed the recipe, the titles diverge. We log
    # but do NOT fail — the recipe_id is authoritative; the title is cosmetic.
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
    # Thin two-step helper: load the DB record, then compile it.
    # Split this way so _load_cookbook_authored_recipe_records can batch-load
    # and then call _compile_authored_raw_recipe_from_record directly without
    # re-querying the DB once per recipe.
    record = await _load_authored_recipe_record(selection)
    return await _compile_authored_raw_recipe_from_record(selection, record)


async def _build_authored_raw_recipe(concept: DinnerConcept) -> RawRecipe:
    if concept.selected_authored_recipe is None:
        # This guard should never be reached in production because the session
        # creation API validates this combination, but it prevents a confusing
        # AttributeError if state is somehow malformed.
        raise ValueError("selected_authored_recipe is required when concept_source is 'authored'")
    return await _compile_authored_raw_recipe(concept.selected_authored_recipe)


async def _build_planner_authored_anchor_raw_recipe(concept: DinnerConcept) -> RawRecipe:
    if concept.planner_authored_recipe_anchor is None:
        raise ValueError("planner_authored_recipe_anchor is required when concept_source is 'planner_authored_anchor'")
    return await _compile_authored_raw_recipe(concept.planner_authored_recipe_anchor)


async def build_authored_raw_recipes(concept: DinnerConcept) -> list[RawRecipe]:
    # Single-recipe authored path — returns a one-element list for interface
    # consistency with build_cookbook_raw_recipes (callers always get a list).
    if concept.concept_source != "authored":
        return []
    return [await _build_authored_raw_recipe(concept)]


async def build_planner_authored_anchor_raw_recipes(concept: DinnerConcept) -> list[RawRecipe]:
    # Planner-authored-anchor: returns the compiled anchor as a one-element list.
    # The node function then computes complement_count = recipe_count - 1 and
    # asks Claude to generate only the remaining dishes.
    if concept.concept_source != "planner_authored_anchor":
        return []
    return [await _build_planner_authored_anchor_raw_recipe(concept)]


async def build_planner_cookbook_target_raw_recipes(concept: DinnerConcept) -> list[RawRecipe]:
    # Planner-cookbook-target: compiles ALL authored recipes in the target
    # cookbook (not just one anchor). The node function slices to recipe_count
    # after this returns. Recipes that fail to compile are silently skipped with
    # their errors collected — partial success is acceptable here because the node
    # will fail anyway if zero recipes compiled.
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
        # Build a transient anchor selection object so we can reuse
        # _compile_authored_raw_recipe_from_record without duplicating logic.
        selection = PlannerLibraryAuthoredRecipeAnchor(recipe_id=record.recipe_id, title=record.title)
        try:
            compiled_recipes.append(await _compile_authored_raw_recipe_from_record(selection, record))
        except ValueError as exc:
            # A single malformed recipe doesn't abort the whole cookbook —
            # collect the error and continue so good recipes still get used.
            compile_errors.append(str(exc))

    if not compiled_recipes:
        # All recipes in the cookbook failed to compile — this is unrecoverable
        # because we have nothing to seed the pipeline with.
        raise ValueError(
            f"Planner cookbook target {target.name!r} ({target.cookbook_id}) did not contain any authored recipes that could compile into scheduling inputs. "
            + "; ".join(compile_errors)
        )

    return compiled_recipes


async def build_planner_catalog_cookbook_raw_recipes(concept: DinnerConcept) -> list[RawRecipe]:
    # Planner-catalog-cookbook: loads deterministic platform-managed runtime
    # seed recipes from the catalog seam. This stays explicitly separate from
    # planner_cookbook_target so platform inventory never reuses private cookbook
    # tables or authored-recipe compilation semantics.
    if concept.concept_source != "planner_catalog_cookbook":
        return []

    catalog_reference = concept.planner_catalog_cookbook
    if catalog_reference is None:
        raise ValueError(
            "planner_catalog_cookbook is required when concept_source is 'planner_catalog_cookbook'"
        )

    return load_catalog_runtime_seed_recipes(catalog_reference.catalog_cookbook_id)


# ── LLM factory (mockable seam) ─────────────────────────────────────────────


def _create_llm() -> ChatAnthropic:
    """
    Creates the ChatAnthropic instance. Extracted as a separate function so
    tests can patch graph.nodes.generator._create_llm to bypass the real API.
    """
    # Monkeypatching seam: tests replace this function with a lambda that returns
    # a mock LLM, allowing all node logic to execute without a real API call.
    # max_tokens=4096 is intentionally generous — recipe generation can produce
    # several thousand tokens of structured JSON (5 recipes × ~800 tokens each).
    settings = get_settings()
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",  # type: ignore[call-arg]
        api_key=settings.anthropic_api_key,
        max_tokens=4096,  # type: ignore[call-arg]
    )


async def _invoke_recipe_generation(
    *,
    system_prompt: str,
    human_prompt: str,
) -> tuple[RecipeGenerationOutput, dict]:
    """Run the structured LLM recipe generation path and return output + token usage."""
    llm = _create_llm()
    # with_structured_output wraps the LLM with a JSON schema enforcer so the
    # response is guaranteed to parse into RecipeGenerationOutput. LangChain
    # handles the tool-calling / constrained-decoding mechanism internally.
    chain = llm.with_structured_output(RecipeGenerationOutput)

    # llm_retry is a decorator that adds exponential backoff for transient API
    # errors (rate limits, timeouts). Defined as an inner async function so
    # the decorator applies at call time (needed for async + retry semantics).
    @llm_retry
    async def _invoke_llm():
        return await chain.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt),
            ]
        )

    result = cast(RecipeGenerationOutput, await _invoke_llm())
    # extract_token_usage reads the LangChain response metadata to get prompt +
    # completion token counts for cost tracking. The "recipe_generator" label
    # tags these entries in the usage log for attribution.
    usage = extract_token_usage(result, "recipe_generator")
    return result, usage


async def _invoke_recipe_generation_candidates(
    *,
    system_prompt: str,
    human_prompt: str,
    candidate_count: int,
) -> tuple[list[RecipeGenerationOutput], list[dict]]:
    """Generate a bounded set of candidate menus while preserving per-call token usage."""
    # Runs N independent LLM calls to get N candidate menus. Because Claude's
    # output is stochastic, multiple samples increase the probability that at
    # least one candidate is oven-compatible. Each call is fully independent —
    # no seeding or temperature manipulation is needed because the model already
    # has non-zero temperature by default.
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
    # Selects the best candidate menu from the pool by oven-compatibility score.
    # anchor_recipes are prepended before scoring so that mixed-origin menus
    # (where the anchor is deterministic and the candidates are LLM-generated)
    # score the FULL combined menu — the anchor's oven usage counts against the
    # candidates' temperatures.
    if not candidate_results:
        raise ValueError("Recipe generation did not return any candidate menus")

    seeded_anchor_recipes = list(anchor_recipes or [])
    # Store (score, index, candidate, score_details) so we can break ties
    # deterministically by original generation order (index).
    scored_candidates: list[tuple[int, int, RecipeGenerationOutput, dict]] = []
    for index, candidate in enumerate(candidate_results):
        recipes = [*seeded_anchor_recipes, *candidate.recipes]
        score_details = _score_menu_oven_compatibility(recipes=recipes, has_second_oven=has_second_oven)
        scored_candidates.append((score_details["score"], index, candidate, score_details))

    # min() by (score, index) — lowest score wins; ties broken by generation order
    # so the first-generated candidate is preferred when scores are equal.
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
    # Orchestrates the full multi-sample → rank → select pipeline.
    # Hard-codes candidate_count=3 — empirically, 3 samples captures most of
    # the variance in Claude's menu choices without tripling the API cost.
    # Returns (best_candidate, all_usages, score_details) so callers can log
    # token usage for all 3 calls, not just the winning one.
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


# ── Retry-attempt state helpers ──────────────────────────────────────────────


def _build_generation_attempt_record(
    *,
    attempt: int,
    recipes: list[RawRecipe],
    retry_reason: GenerationRetryReason | None,
) -> dict:
    # Serialises an audit record for this generation attempt into a JSON-safe dict
    # for storage in generation_history (GRASPState). The history lets downstream
    # nodes and the renderer explain to the user why dishes changed between attempts.
    # trigger="auto_repair" when this is a retry driven by the scheduler conflict;
    # trigger="initial" for the first-ever generation.
    return GenerationAttemptRecord(
        attempt=attempt,
        trigger="auto_repair" if retry_reason is not None else "initial",
        recipe_names=[recipe.name for recipe in recipes],
        retry_reason=retry_reason,
    ).model_dump(mode="json")


# ── Node function ────────────────────────────────────────────────────────────


async def recipe_generator_node(state: GRASPState) -> dict:
    """
    Real recipe generator node. Calls Claude to generate RawRecipe objects.

    Returns partial GRASPState dict with raw_recipes (replace semantics).
    On failure, returns empty raw_recipes + fatal NodeError.
    """
    try:
        # Deserialise the concept from GRASPState's raw dict representation —
        # LangGraph serialises all state fields as plain dicts/JSON between node
        # invocations, so we always re-validate from the state payload rather
        # than trusting it to already be a typed object.
        concept = DinnerConcept.model_validate(state["concept"])  # type: ignore[typeddict-item]
        kitchen_config = state.get("kitchen_config", {})
        equipment = state.get("equipment", [])

        # ── Path 1: cookbook concept_source ──────────────────────────────────
        # Fully deterministic — no LLM call. Converts persisted Pinecone chunk
        # selections into RawRecipe objects and returns immediately.
        if concept.concept_source == "cookbook":
            cookbook_recipes = build_cookbook_raw_recipes(concept)
            # The DAG merger's oven-conflict classifier uses course="entree" to
            # identify the anchor dish. Cookbook-selected recipes may not have a
            # course tag if the chunk wasn't annotated during ingestion, so we
            # inject it here as a safe default. The first recipe in the user's
            # selection order is treated as the main dish.
            if cookbook_recipes and cookbook_recipes[0].course is None:
                cookbook_recipes[0] = cookbook_recipes[0].model_copy(update={"course": "entree"})
            logger.info("Seeded %d cookbook recipes from persisted selections", len(cookbook_recipes))
            return {
                "raw_recipes": [recipe.model_dump(mode="json") for recipe in cookbook_recipes],
            }

        # ── Path 2: authored concept_source ──────────────────────────────────
        # Single authored recipe — deterministic DB lookup + compile, no LLM.
        if concept.concept_source == "authored":
            authored_recipes = await build_authored_raw_recipes(concept)
            # Same entree-tagging logic as cookbook path above.
            if authored_recipes and authored_recipes[0].course is None:
                authored_recipes[0] = authored_recipes[0].model_copy(update={"course": "entree"})
            selected = concept.selected_authored_recipe
            logger.info(
                "Seeded %d authored recipe from persisted selection %s",
                len(authored_recipes),
                selected.recipe_id if selected else "unknown",
            )
            return {
                "raw_recipes": [recipe.model_dump(mode="json") for recipe in authored_recipes],
            }

        # recipe_count is needed by both the planner_authored_anchor path (to
        # compute how many complements to generate) and the free-text path (to
        # request the right number of courses from Claude).
        recipe_count = _resolve_recipe_count(concept)

        # ── Path 3: planner_authored_anchor concept_source ───────────────────
        # Mixed-origin: the anchor is compiled deterministically; Claude generates
        # only the remaining complement_count recipes. This preserves the user's
        # chosen dish while filling out the rest of the menu with AI suggestions.
        if concept.concept_source == "planner_authored_anchor":
            anchor_recipes = await build_planner_authored_anchor_raw_recipes(concept)
            anchor_recipe = anchor_recipes[0]
            # Inject entree tag before passing to mixed-origin prompt so Claude
            # knows not to generate another entree and to assign correct course
            # values (appetizer, side, dessert) to the complements.
            if anchor_recipe.course is None:
                anchor_recipe = anchor_recipe.model_copy(update={"course": "entree"})
                anchor_recipes[0] = anchor_recipe
            complement_count = max(0, recipe_count - len(anchor_recipes))

            # Edge case: the anchor alone satisfies recipe_count (e.g. casual
            # breakfast with dish_count=1). Skip LLM and return immediately.
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
            # Multi-candidate ranking only makes sense when we need ≥ 2 complements;
            # a single-complement menu doesn't benefit from multi-sampling because
            # the oven-score difference between candidates is negligible at count=1.
            # Similarly, if the anchor's provenance is "generated" (rare: a previous
            # LLM-generated recipe was pinned as anchor), use ranked candidates to
            # better complement it; otherwise a single call suffices.
            if complement_count == 1 and len(anchor_recipes) > 1:
                result, usage = await _invoke_recipe_generation(system_prompt=system_prompt, human_prompt=human_prompt)
                usages = [usage]
            elif anchor_recipe.provenance.kind == "generated":
                result, usages, _score_details = await _generate_ranked_recipe_candidates(
                    system_prompt=system_prompt,
                    human_prompt=human_prompt,
                    kitchen_config=kitchen_config,
                    anchor_recipes=anchor_recipes,
                )
            else:
                result, usage = await _invoke_recipe_generation(system_prompt=system_prompt, human_prompt=human_prompt)
                usages = [usage]
            # Anchor goes first — establishes the menu's main dish ordering for
            # downstream renderer and schedule display.
            all_recipes = [*anchor_recipes, *result.recipes]
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

        # ── Path 4: planner_cookbook_target concept_source ───────────────────
        # Loads all authored recipes in a planner cookbook, then either seeds them
        # directly (strict mode) or uses the first as an anchor and generates
        # complements (biased mode). Strict mode is fully deterministic.
        if concept.concept_source == "planner_cookbook_target":
            target = concept.planner_cookbook_target
            cookbook_recipes = await build_planner_cookbook_target_raw_recipes(concept)
            # Default to STRICT if target is somehow None (shouldn't be, but keeps
            # the code safe against malformed state).
            cookbook_mode = target.mode if target is not None else PlannerLibraryCookbookPlanningMode.STRICT

            if cookbook_mode == PlannerLibraryCookbookPlanningMode.STRICT:
                # Strict mode: take the first recipe_count recipes from the cookbook
                # as-is. No LLM involvement. Slice rather than truncate so we don't
                # mutate the compiled list.
                seeded_recipes = cookbook_recipes[:recipe_count]
                # Tag first recipe as entree for DAG merger conflict detection.
                if seeded_recipes and seeded_recipes[0].course is None:
                    seeded_recipes[0] = seeded_recipes[0].model_copy(update={"course": "entree"})
                logger.info(
                    "Seeded %d planner cookbook recipes from %r in strict mode",
                    len(seeded_recipes),
                    target.name if target else "unknown cookbook",
                )
                return {
                    "raw_recipes": [recipe.model_dump(mode="json") for recipe in seeded_recipes],
                }

            # Biased mode: seed from the cookbook, then ask Claude to generate
            # additional complementary recipes. Same mixed-origin pattern as
            # planner_authored_anchor above.
            seeded_recipes = cookbook_recipes[:recipe_count]
            anchor_recipe = seeded_recipes[0]
            if anchor_recipe.course is None:
                anchor_recipe = anchor_recipe.model_copy(update={"course": "entree"})
                seeded_recipes[0] = anchor_recipe
            complement_count = max(0, recipe_count - len(seeded_recipes))
            if complement_count == 0:
                # Cookbook alone satisfies recipe_count — no LLM needed.
                logger.info(
                    "Seeded %d planner cookbook recipes from %r in biased mode with no complementary generation required",
                    len(seeded_recipes),
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
            # Same single-vs-multi-candidate logic as planner_authored_anchor path.
            if complement_count == 1 and len(seeded_recipes) > 1:
                result, usage = await _invoke_recipe_generation(system_prompt=system_prompt, human_prompt=human_prompt)
                usages = [usage]
            elif anchor_recipe.provenance.kind == "generated":
                result, usages, _score_details = await _generate_ranked_recipe_candidates(
                    system_prompt=system_prompt,
                    human_prompt=human_prompt,
                    kitchen_config=kitchen_config,
                    anchor_recipes=seeded_recipes,
                )
            else:
                result, usage = await _invoke_recipe_generation(system_prompt=system_prompt, human_prompt=human_prompt)
                usages = [usage]
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

        # ── Path 5: planner_catalog_cookbook concept_source ────────────────
        # Platform-catalog path: seed deterministic runtime recipes from the
        # catalog seam. Included catalog selections execute seed-only; preview
        # selections expose the same seed payloads but allow complementary LLM
        # generation so the runtime path stays meaningful without pretending the
        # preview inventory is a complete private cookbook.
        if concept.concept_source == "planner_catalog_cookbook":
            catalog_reference = concept.planner_catalog_cookbook
            catalog_seed_recipes = await build_planner_catalog_cookbook_raw_recipes(concept)
            if catalog_seed_recipes and catalog_seed_recipes[0].course is None:
                catalog_seed_recipes[0] = catalog_seed_recipes[0].model_copy(update={"course": "entree"})

            access_state = catalog_reference.access_state if catalog_reference is not None else "included"
            if access_state == "included":
                seeded_recipes = catalog_seed_recipes[:recipe_count]
                logger.info(
                    "Seeded %d planner catalog recipes from %r in included mode",
                    len(seeded_recipes),
                    catalog_reference.slug if catalog_reference else "unknown catalog cookbook",
                )
                return {
                    "raw_recipes": [recipe.model_dump(mode="json") for recipe in seeded_recipes],
                }

            seeded_recipes = catalog_seed_recipes[:1]
            anchor_recipe = seeded_recipes[0]
            complement_count = max(0, recipe_count - len(seeded_recipes))
            if complement_count == 0:
                logger.info(
                    "Seeded planner catalog preview anchor %r from %r with no complementary generation required",
                    anchor_recipe.name,
                    catalog_reference.slug if catalog_reference else "unknown catalog cookbook",
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
                "Seeded planner catalog preview anchor %r from %r and generating %d complementary candidates",
                anchor_recipe.name,
                catalog_reference.slug if catalog_reference else "unknown catalog cookbook",
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
                "Seeded planner catalog cookbook %r and selected %d complementary recipes: %s",
                catalog_reference.slug if catalog_reference else "unknown catalog cookbook",
                len(result.recipes),
                [r.name for r in result.recipes],
            )
            return {
                "raw_recipes": [recipe.model_dump(mode="json") for recipe in all_recipes],
                "token_usage": usages,
            }

        # ── Path 6: free_text / default LLM generation path ─────────────────
        # All other concept_source values (free_text, None, etc.) fall through
        # to here — Claude generates all recipes from scratch.

        # Check if this is a retry triggered by dag_merger detecting an
        # irreconcilable oven conflict on a previous generation attempt.
        # generation_retry_reason is set by dag_merger before routing back to
        # generator; it's cleared in our return dict once we consume it.
        retry_reason_payload = state.get("generation_retry_reason")
        retry_reason = (
            GenerationRetryReason.model_validate(retry_reason_payload) if retry_reason_payload is not None else None
        )
        # generation_attempt starts at 1 for the first run and increments each
        # time dag_merger reroutes here. Used for audit logging and to embed
        # "Retry attempt N" in the corrective prompt so Claude understands context.
        generation_attempt = max(1, int(state.get("generation_attempt", 1)))
        # generation_history is an accumulator across attempts — we append each
        # attempt record rather than replace, so the full retry trail is preserved.
        generation_history = list(state.get("generation_history", []))

        if retry_reason is not None:
            # Retry path: inject the scheduler's conflict context into the prompt
            # so Claude specifically avoids the same temperature-conflict pattern.
            system_prompt = _build_retry_system_prompt(
                concept=concept,
                kitchen_config=kitchen_config,
                equipment=equipment,
                recipe_count=recipe_count,
                retry_reason=retry_reason,
            )
            human_prompt = _build_retry_human_prompt(recipe_count, concept, retry_reason)
            logger.info(
                "Corrective regeneration attempt %d for %s %s using blocking recipes=%s classification=%s gap=%s",
                retry_reason.attempt + 1,
                concept.occasion.value,
                concept.meal_type.value,
                retry_reason.summary.blocking_recipe_names,
                retry_reason.summary.classification,
                retry_reason.summary.temperature_gap_f,
            )
        else:
            # First-attempt free-text generation — no conflict context available.
            system_prompt = _build_system_prompt(concept, kitchen_config, equipment, recipe_count)
            human_prompt = _build_human_prompt(recipe_count, concept)
            logger.info(
                "Generating %d recipe candidates for %s %s",
                recipe_count,
                concept.occasion.value,
                concept.meal_type.value,
            )

        # Always use multi-candidate generation on the free-text path — 3 candidates,
        # score by oven compatibility, pick the best. This is the primary defence
        # against oven conflicts before they reach the scheduler.
        result, usages, _score_details = await _generate_ranked_recipe_candidates(
            system_prompt=system_prompt,
            human_prompt=human_prompt,
            kitchen_config=kitchen_config,
        )

        logger.info("Selected %d generated recipes: %s", len(result.recipes), [r.name for r in result.recipes])
        # LLM occasionally ignores the explicit count — warn rather than error
        # so the pipeline continues with a partial menu rather than crashing.
        if len(result.recipes) < recipe_count:
            logger.warning(
                "LLM returned %d recipes but %d were requested — menu may be incomplete",
                len(result.recipes),
                recipe_count,
            )
        # Build and upsert the history record for this attempt. If the state
        # already has an entry for the current attempt number (e.g. this is a
        # resume after checkpoint), replace it rather than duplicating. Otherwise
        # append a new entry.
        history_record = _build_generation_attempt_record(
            attempt=generation_attempt,
            recipes=result.recipes,
            retry_reason=retry_reason,
        )
        if generation_history and generation_history[-1].get("attempt") == generation_attempt:
            # Same attempt already recorded (idempotent re-run) — overwrite it.
            generation_history[-1] = history_record
        else:
            generation_history.append(history_record)

        # Return raw_recipes as dicts (replace semantics)
        # Clearing enriched_recipes, validated_recipes, recipe_dags, merged_dag,
        # and schedule here enforces replace semantics for the whole pipeline
        # segment downstream of generator — if this node runs again (retry), the
        # stale enriched/scheduled data from the previous attempt is discarded.
        # generation_retry_reason is explicitly nulled to signal "consumed".
        return {
            "raw_recipes": [r.model_dump() for r in result.recipes],
            "token_usage": usages,
            "generation_history": generation_history,
            "generation_retry_reason": None,  # consumed — prevent dag_merger from seeing stale reason
            "enriched_recipes": [],
            "validated_recipes": [],
            "recipe_dags": [],
            "merged_dag": None,
            "schedule": None,
        }

    except Exception as exc:
        # Generator failure is always fatal (recoverable=False) — there is no
        # way to proceed through enrichment, DAG building, or scheduling without
        # at least one recipe. The LangGraph runner will halt the pipeline and
        # surface this error to the API layer.
        #
        # Error type classification:
        #   - Timeout errors → LLM_TIMEOUT (so monitoring can track API latency issues)
        #   - ValueError → VALIDATION_FAILURE (structured problem: bad state, missing recipe, etc.)
        #   - Everything else → LLM_PARSE_FAILURE (unexpected Claude response shape)
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
            recoverable=False,  # no recipes = pipeline cannot continue
            message=f"{exc_type}: {exc}",
            metadata={"exception_type": exc_type},
        )

        # Return empty raw_recipes so GRASPState stays schema-valid even on
        # failure — downstream guards check for empty lists rather than None.
        return {
            "raw_recipes": [],
            "errors": [error.model_dump(mode="json")],
        }
