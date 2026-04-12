"""
graph/nodes/enricher.py

LLM-only recipe enricher — structured step conversion for the active product path.

M026 removed cookbook-specific retrieval from the enrichment stage. The enricher
now reads raw recipes from GRASPState and uses Claude to convert flat step text
into structured RecipeStep objects with timing, resource tags, dependency edges,
and deterministic ingredient/preheat augmentation.

Error handling: per-recipe recoverable. If one recipe fails enrichment,
it is dropped and the pipeline continues with survivors. If ALL recipes
fail, the error is fatal (recoverable=False) — nothing to validate or
schedule.

IDEMPOTENCY: Returns enriched_recipes as a NEW list (not appended).
Replace semantics — same contract as generator (§2.10).

Mockable seam:
  _create_llm() — returns ChatAnthropic instance

Tests patch the LLM factory/invocation to bypass external APIs.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable
from typing import cast

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.core.llm import extract_token_usage, is_timeout_error, llm_retry
from app.core.settings import get_settings
from app.models.enums import ErrorType, Resource
from app.models.errors import NodeError
from app.models.pipeline import GRASPState
from app.models.recipe import EnrichedRecipe, RawRecipe, RecipeStep

logger = logging.getLogger(__name__)

# Allowed chunk types for RAG advisory context.
# "index" and "catalog" chunk types are filtered out — they contain
# page numbers and cross-reference lists, not culinary knowledge.
# Only narrative knowledge (technique, recipe, tip, narrative) is useful
# for enriching step descriptions.
ALLOWED_RAG_CHUNK_TYPES = {
    "recipe",
    "ingredient_list",
    "narrative",
    "technique",
    "tip",
}


# ── Ingredient parsing and unit normalization ────────────────────────────────


def _parse_and_normalize_ingredients(raw_recipe: RawRecipe) -> list:
    """
    Parse ingredients from raw_recipe using ingredient-parser-nlp and normalize
    units to canonical system using Pint.

    Why normalize units?
      The generator produces ingredient quantities in whatever unit Claude chose
      (cups, tablespoons, grams, etc.). Normalization to canonical units lets the
      prep-merging logic in the dag_merger compare quantities across recipes
      (e.g. "1 cup flour" + "2 tablespoons flour" → "1 cup + 2 tbsp flour").

    Canonical units:
      - Volume: cup > tablespoon > teaspoon (prefers largest unit with magnitude ≥ 1)
      - Weight: gram

    Returns list of IngredientUse dicts ready for RecipeStep.ingredient_uses.

    Silent fallback contract:
      - Unconvertible units: quantity_canonical=None, fallback_reason set.
      - Dimensionless quantities (e.g. "2 eggs"): stored as-is, no conversion.
      - Parser failures: falls back to original quantity string from raw_recipe.
    The pipeline never fails due to ingredient parsing — it degrades gracefully
    to raw quantity strings when normalization isn't possible.
    """
    from ingredient_parser import parse_ingredient
    from pint import UnitRegistry, DimensionalityError, UndefinedUnitError

    ureg = UnitRegistry()
    results = []

    # Volume canonical order: try cup first (largest), fall back to tbsp, then tsp.
    # This ensures "3 teaspoons" stays as teaspoons rather than converting to
    # a tiny fraction of a cup.
    CANONICAL_VOLUME = ["cup", "tablespoon", "teaspoon"]
    CANONICAL_WEIGHT = "gram"

    for ing in raw_recipe.ingredients:
        # Build raw string for the ingredient parser
        raw_str = f"{ing.quantity} {ing.name}".strip()

        try:
            parsed = parse_ingredient(raw_str)

            # ingredient_parser returns lists for name, preparation, amount.
            # Take the first element of each (most recipes have one name/prep per ingredient).
            ingredient_name = parsed.name[0].text if parsed.name and len(parsed.name) > 0 else ing.name  # type: ignore[index,arg-type]
            prep_method = parsed.preparation[0].text if parsed.preparation and len(parsed.preparation) > 0 else ing.preparation  # type: ignore[index,arg-type]
            quantity_original = ing.quantity

            quantity_canonical = None
            unit_canonical = None
            fallback_reason = None

            if parsed.amount and len(parsed.amount) > 0:
                amount = parsed.amount[0]

                if amount.quantity is None:  # type: ignore[union-attr]
                    # No numeric quantity — store as fallback (e.g. "a pinch of salt")
                    fallback_reason = "no quantity specified"
                    results.append({
                        "ingredient_name": ingredient_name,
                        "prep_method": prep_method,
                        "quantity_canonical": quantity_canonical,
                        "unit_canonical": unit_canonical,
                        "quantity_original": quantity_original,
                        "fallback_reason": fallback_reason,
                    })
                    continue

                quantity = float(amount.quantity)  # type: ignore[union-attr]
                unit_obj = amount.unit  # type: ignore[union-attr]

                if unit_obj is None or str(unit_obj) == "" or str(unit_obj) == "dimensionless":
                    # Dimensionless quantity (e.g., "2 eggs") — no unit conversion possible.
                    # Stored as-is; the merger can still display "2 eggs" correctly.
                    fallback_reason = "dimensionless quantity"
                    results.append({
                        "ingredient_name": ingredient_name,
                        "prep_method": prep_method,
                        "quantity_canonical": quantity_canonical,
                        "unit_canonical": unit_canonical,
                        "quantity_original": quantity_original,
                        "fallback_reason": fallback_reason,
                    })
                    continue

                try:
                    pint_quantity = quantity * unit_obj  # type: ignore[operator]

                    converted = False

                    # Try volume units largest-first. Stop at first unit where
                    # the converted magnitude is ≥ 1. This avoids "0.06 cups" for
                    # 1 tablespoon — we'd prefer to display "1 tablespoon" instead.
                    for vol_unit in CANONICAL_VOLUME:
                        try:
                            converted_qty = pint_quantity.to(vol_unit)  # type: ignore[union-attr]
                            magnitude = float(converted_qty.magnitude)  # type: ignore[arg-type]

                            if magnitude >= 1.0:
                                quantity_canonical = magnitude
                                unit_canonical = vol_unit
                                converted = True
                                break
                        except DimensionalityError:
                            continue

                    # If all volume units give magnitude < 1, use teaspoon (smallest)
                    # to avoid displaying fractions like "0.001 cups".
                    if not converted:
                        for vol_unit in reversed(CANONICAL_VOLUME):
                            try:
                                converted_qty = pint_quantity.to(vol_unit)  # type: ignore[union-attr]
                                quantity_canonical = float(converted_qty.magnitude)  # type: ignore[arg-type]
                                unit_canonical = vol_unit
                                converted = True
                                break
                            except DimensionalityError:
                                continue

                    # If volume conversion failed entirely, try weight.
                    if not converted:
                        try:
                            converted_qty = pint_quantity.to(CANONICAL_WEIGHT)  # type: ignore[union-attr]
                            quantity_canonical = float(converted_qty.magnitude)  # type: ignore[arg-type]
                            unit_canonical = CANONICAL_WEIGHT
                            converted = True
                        except DimensionalityError:
                            fallback_reason = f"unconvertible unit: '{unit_obj}'"

                except (UndefinedUnitError, AttributeError, ValueError, TypeError) as e:
                    fallback_reason = f"unconvertible unit: '{unit_obj}'"
            else:
                fallback_reason = "no quantity specified"

            results.append({
                "ingredient_name": ingredient_name,
                "prep_method": prep_method,
                "quantity_canonical": quantity_canonical,
                "unit_canonical": unit_canonical,
                "quantity_original": quantity_original,
                "fallback_reason": fallback_reason,
            })

        except Exception as e:
            # ingredient_parser failed completely — fall back to raw data.
            # This ensures one bad ingredient string doesn't drop the whole recipe.
            logger.warning("Ingredient parsing failed for '%s': %s", raw_str, e)
            results.append({
                "ingredient_name": ing.name,
                "prep_method": ing.preparation,
                "quantity_canonical": None,
                "unit_canonical": None,
                "quantity_original": ing.quantity,
                "fallback_reason": f"parsing failed: {type(e).__name__}",
            })

    return results


def _link_steps_to_ingredients(
    steps: list[RecipeStep],
    normalized_ingredients: list[dict],
    raw_recipe: RawRecipe,
) -> list[RecipeStep]:
    """
    Parse ingredient tags from LLM output ("Uses: ingredient1, ingredient2")
    and link to normalized_ingredients, populating RecipeStep.ingredient_uses.

    Why "Uses: X, Y" format?
      The enrichment prompt instructs Claude to include "Uses: <ingredient list>"
      in each step description. This is a cheap structured extraction — we parse
      it out here rather than adding a separate LLM structured output field.
      Claude reliably follows this convention when explicitly instructed.

    Exact name matching only (no fuzzy matching):
      Fuzzy matching risks linking the wrong ingredient (e.g. "butter" matching
      "peanut butter"). Exact match on the normalized name is safer and
      sufficient since Claude uses the same ingredient names from the prompt.

    Returns new list of RecipeStep objects with ingredient_uses populated.
    Steps with no "Uses:" tag get empty ingredient_uses — not an error.
    """
    from app.models.recipe import IngredientUse

    # Build a case-insensitive lookup from ingredient name to its normalized metadata dict.
    ingredient_lookup = {
        ing_dict["ingredient_name"].lower().strip(): ing_dict
        for ing_dict in normalized_ingredients
    }

    updated_steps = []
    for step in steps:
        uses_pattern = r"Uses:\s*([^\n]+)"
        match = re.search(uses_pattern, step.description, re.IGNORECASE)

        ingredient_uses = []
        if match:
            ing_names_str = match.group(1).strip()
            ing_names = [name.strip().lower() for name in ing_names_str.split(",")]

            for ing_name in ing_names:
                if ing_name in ingredient_lookup:
                    ing_data = ingredient_lookup[ing_name]
                    ingredient_uses.append(
                        IngredientUse(
                            ingredient_name=ing_data["ingredient_name"],
                            prep_method=ing_data["prep_method"],
                            quantity_canonical=ing_data["quantity_canonical"],
                            unit_canonical=ing_data["unit_canonical"],
                            quantity_original=ing_data["quantity_original"],
                            fallback_reason=ing_data["fallback_reason"],
                        )
                    )

        # Build a new RecipeStep instance with ingredient_uses populated.
        # RecipeStep is immutable (Pydantic model) — must construct a new one.
        updated_step = RecipeStep(
            step_id=step.step_id,
            description=step.description,
            duration_minutes=step.duration_minutes,
            duration_max=step.duration_max,
            resource=step.resource,
            depends_on=step.depends_on,
            can_be_done_ahead=step.can_be_done_ahead,
            prep_ahead_window=step.prep_ahead_window,
            prep_ahead_notes=step.prep_ahead_notes,
            ingredient_uses=ingredient_uses,
            oven_temp_f=step.oven_temp_f,
        )
        updated_steps.append(updated_step)

    return updated_steps


# ── Structured output wrapper ────────────────────────────────────────────────


class StepEnrichmentOutput(BaseModel):
    """Wrapper for LangChain structured output. Claude returns this shape.

    LangChain's with_structured_output() forces Claude to produce output
    that validates against this model. If Claude produces invalid output
    (wrong types, missing required fields), LangChain raises a validation
    error that the llm_retry decorator treats as non-retryable.
    """

    steps: list[RecipeStep]
    chef_notes: str
    techniques_used: list[str]


# ── Slug generation ──────────────────────────────────────────────────────────


def _generate_recipe_slug(name: str) -> str:
    """
    Convert recipe name to a URL-safe slug for step_id generation.
    'Braised Short Ribs' → 'braised_short_ribs'

    Step IDs follow the convention: {slug}_step_{n}
    This slug must match the one used in dag_builder._generate_recipe_slug()
    for step prefix matching to work correctly during scheduling.
    """
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ── Prompt builders ──────────────────────────────────────────────────────────


def _build_enrichment_context() -> str:
    """Return the fixed enrichment-context block for the active no-retrieval contract."""
    return "No external cookbook context is provided. Use the raw recipe and your general culinary knowledge only."


def _format_rag_context(chunks: list[dict]) -> str:
    """
    Format retrieved RAG chunks for inclusion in the enrichment prompt.

    The chunks are labeled [RECIPE #N], [TECHNIQUE #N] etc. so Claude can
    reference them when writing chef_notes. The labels make it clear to Claude
    that these are advisory excerpts, not authoritative recipe sources.
    """
    if not chunks:
        return "No cookbook-specific context available. Use your general culinary knowledge."

    lines = []
    for i, chunk in enumerate(chunks, 1):
        chunk_type = chunk.get("chunk_type", "unknown").upper()
        text = chunk.get("text", "").strip()
        if text:
            lines.append(f"[{chunk_type} #{i}]\n{text}")

    return "\n\n".join(lines)


def _build_enrichment_prompt(
    raw_recipe: RawRecipe,
    slug: str,
) -> str:
    """Build the system prompt for step enrichment.

    The prompt is long and detailed by design — Claude needs explicit
    instructions for step_id format, resource types, dependency rules,
    timing guidelines, prep-ahead criteria, and oven temperature extraction.
    Ambiguous instructions produce inconsistent output that breaks the scheduler.
    """
    enrichment_context_text = _build_enrichment_context()

    steps_text = "\n".join(f"  {i + 1}. {step}" for i, step in enumerate(raw_recipe.steps))
    ingredients_text = "\n".join(
        f"  - {ing.name}: {ing.quantity}" + (f" ({ing.preparation})" if ing.preparation else "")
        for ing in raw_recipe.ingredients
    )

    return f"""You are GRASP's enrichment engine. Your job is to convert a raw recipe's flat text steps into structured, schedulable RecipeStep objects with precise timing, resource assignments, and dependency edges.

## RAW RECIPE
- Name: {raw_recipe.name}
- Cuisine: {raw_recipe.cuisine}
- Description: {raw_recipe.description}
- Servings: {raw_recipe.servings}
- Estimated total time: {raw_recipe.estimated_total_minutes} minutes

### Ingredients
{ingredients_text}

### Steps (flat text — convert these)
{steps_text}

## STEP ID CONVENTION
Each step_id MUST follow the format: `{slug}_step_{{n}}` where n starts at 1.
Example: `{slug}_step_1`, `{slug}_step_2`, etc.
Generate exactly one RecipeStep per flat text step above, in the same order.

## RESOURCE TYPES
Assign exactly one resource per step:
- **oven** — baking, roasting, braising in oven. Semi-exclusive (limited by oven racks).
- **stovetop** — boiling, simmering, frying, sautéing. Semi-exclusive (limited by burners).
- **passive** — resting, chilling, marinating, proofing, brining. Non-exclusive (always parallelisable).
- **hands** — kneading, shaping, folding, whisking, plating, active prep. Exclusive (one at a time).

## DEPENDENCY RULES
- `depends_on` is a list of step_ids that must complete before this step can start.
- First step has `depends_on: []`.
- Most steps depend on the previous step (linear chain), but identify opportunities for parallelism.
- A step that uses output of a previous step MUST depend on it.

## TIMING GUIDELINES
- `duration_minutes`: realistic estimate for this specific step. Must be > 0.
- `duration_max`: optional upper bound for variable steps (braising, resting). Set to null for deterministic steps. Must be >= duration_minutes if set.
- The sum of critical-path durations should approximate the recipe's estimated_total_minutes ({raw_recipe.estimated_total_minutes} min).

## PREP-AHEAD IDENTIFICATION
- `can_be_done_ahead`: true ONLY for steps that REQUIRE or STRONGLY BENEFIT from extended lead time:
  - Brining (4+ hours immersion)
  - Marinating (4+ hours to penetrate)
  - Making stock, broth, or demi-glace
  - Proofing/fermenting dough
  - Setting gelatin, custard, or ganache overnight
  - Curing or dry-aging
  - Braising that can be done a day ahead and reheated
- `can_be_done_ahead` is FALSE for quick prep tasks — even if they COULD be done ahead:
  - Herb rubs, spice mixes, compound butters
  - Chopping, slicing, dicing vegetables
  - Mixing dry ingredients
  - Toasting spices or nuts
  - Making vinaigrette or simple pan sauces
  - Blanching vegetables
  - Tempering chocolate (must be used immediately)
- `prep_ahead_window`: e.g. "up to 24 hours", "up to 3 days". Only set if can_be_done_ahead is true. Must express hours or days, never minutes.
- `prep_ahead_notes`: brief storage/reheating instructions. Only set if can_be_done_ahead is true.

## ENRICHMENT CONTEXT
The active product path does not retrieve cookbook-specific context during enrichment.
Derive your output from the RAW RECIPE and your general culinary knowledge only.

{enrichment_context_text}

## OVEN TEMPERATURE EXTRACTION
For each step, extract the oven temperature if the step involves oven cooking. Populate the `oven_temp_f` field as follows:
- **Numeric Fahrenheit temperatures**: Extract directly (e.g., "375°F" → 375, "425°F" → 425)
- **Celsius temperatures**: Convert to Fahrenheit using (C × 9/5) + 32 (e.g., "150°C" → 302, "200°C" → 392)
- **Vague heat levels**: Use these predefined ranges:
  - "high heat" or "hot oven" → 437°F
  - "medium heat" or "moderate oven" → 362°F
  - "low heat" or "low oven" → 312°F
- **Non-oven steps**: Set oven_temp_f to null
- **Validation**: All extracted temperatures must be in the 200-550°F range. If a temperature falls outside this range, set to null and note in chef_notes.

## OUTPUT REQUIREMENTS
1. Generate exactly {len(raw_recipe.steps)} RecipeStep objects, one per flat text step.
2. Each step description should be a refined, actionable version of the flat text — add precision (temperatures, visual cues, timing details) but preserve the original intent.
3. For oven steps, populate oven_temp_f according to the oven extraction rules above.
4. chef_notes: 1-2 sentences of practical advice for executing this recipe.
5. techniques_used: list of culinary techniques employed (e.g. "braising", "emulsification", "tempering").
6. Do NOT include oven preheating instructions ("Preheat oven to X°F") in step descriptions. Oven preheating is injected as a separate step by the pipeline. Write oven steps assuming the oven is already at temperature (e.g. "Transfer to the preheated oven. Braise for 45 minutes..." not "Preheat oven to 325°F. Transfer to oven...")."""


# ── LLM factory (mockable seam) ─────────────────────────────────────────────


def _create_llm() -> ChatAnthropic:
    """
    Creates the ChatAnthropic instance. Extracted as a separate function so
    tests can patch graph.nodes.enricher._create_llm to bypass the real API.

    Why a factory function rather than a module-level constant?
      The LLM instance captures the API key at construction time. A module-level
      constant would read the key at import time, before the test can set up
      environment variables. A factory function reads the key on first call,
      which is after any test fixtures have run.
    """
    settings = get_settings()
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",  # type: ignore[call-arg]
        api_key=settings.anthropic_api_key,
        max_tokens=4096,  # type: ignore[call-arg]
    )


# ── Preheat injection ────────────────────────────────────────────────────────


def _strip_preheat_from_descriptions(steps: list[RecipeStep]) -> list[RecipeStep]:
    """
    Remove leading preheat-oven sentences from step descriptions.

    Why strip preheat from descriptions?
      The enrichment prompt explicitly tells Claude not to include preheat
      instructions, but Claude occasionally does it anyway. The pipeline
      injects its own synthetic preheat step (_inject_preheat_steps), so
      embedded preheat text in descriptions causes two problems:
        1. The chef sees "Preheat to 375°F" twice (once in description, once as step)
        2. If two recipes have different oven temps, both preheat instructions
           appear back-to-back, which is confusing.

    Only strips leading preheat sentences. If "Preheat oven" appears mid-step,
    it's left alone — that's a different context (e.g. "while you preheat the oven").
    """
    pattern = re.compile(r'^Preheat oven[^.]*\.\s*', re.IGNORECASE)
    result = []
    for step in steps:
        stripped = pattern.sub('', step.description).strip()
        if stripped != step.description:
            step = step.model_copy(update={"description": stripped})
        result.append(step)
    return result


def _inject_preheat_steps(steps: list[RecipeStep], slug: str) -> list[RecipeStep]:
    """
    Inject synthetic preheat step before first oven usage.

    Why inject preheat synthetically instead of relying on Claude?
      1. Timing precision: preheat takes ~12 minutes and must complete BEFORE
         the first oven step. Without an explicit preheat step, the scheduler
         doesn't know to start the oven 12 minutes early.
      2. Cross-recipe oven conflict detection: the dag_merger uses oven step
         intervals to detect temperature conflicts. A synthetic preheat step
         with oven_temp_f set lets the merger detect that a 325°F braise and
         a 450°F roast can't share one oven — even if the recipe steps overlap.
      3. Consistency: Claude sometimes forgets preheat, sometimes includes it.
         Injecting it deterministically here removes that variability.

    Injection contract:
      - One preheat per recipe (before the FIRST oven step with a temperature).
      - step_id: {slug}_preheat_1 — always includes "_preheat_" for is_preheat flag.
      - duration_minutes: 12 (conservative standard residential oven preheat time).
      - The first oven step's depends_on gains preheat_step_id prepended.
      - No preheat if there are no oven steps or if the first oven step has no temp.
    """
    first_oven_idx = None
    first_oven_temp = None

    for idx, step in enumerate(steps):
        if step.resource == Resource.OVEN and step.oven_temp_f is not None:
            first_oven_idx = idx
            first_oven_temp = step.oven_temp_f
            break

    if first_oven_idx is None:
        return steps

    preheat_step = RecipeStep(
        step_id=f"{slug}_preheat_1",
        description=f"Preheat oven to {first_oven_temp}°F",
        duration_minutes=12,
        resource=Resource.OVEN,
        oven_temp_f=first_oven_temp,
        depends_on=[],  # Preheat has no prerequisites — start immediately
        can_be_done_ahead=False,
        prep_ahead_window=None,
        ingredient_uses=[],
    )

    # Wire the first oven step to depend on the preheat completing.
    # Prepend preheat_step_id to preserve any existing dependencies.
    first_oven_step = steps[first_oven_idx]
    steps[first_oven_idx] = first_oven_step.model_copy(
        update={"depends_on": [preheat_step.step_id] + first_oven_step.depends_on}
    )

    # Preheat goes at position 0 — it should start at T+0, not after other steps.
    return [preheat_step] + steps


# ── Per-recipe enrichment ────────────────────────────────────────────────────


async def _enrich_single_recipe(
    raw_recipe: RawRecipe,
) -> tuple[EnrichedRecipe, dict]:
    """
    Enrich a single RawRecipe using LLM-only structuring.

    Steps in order:
      1. Parse + normalize ingredients (deterministic, no I/O)
      2. LLM enrichment with structured output (async, with retry)
      3. Link LLM-tagged ingredients to normalized metadata
      4. Strip embedded preheat from step descriptions
      5. Inject synthetic preheat step

    Raises on failure — the caller (_enrich_one in enrich_recipe_steps_node) catches
    this and marks the recipe as per-recipe recoverable error.

    Returns (EnrichedRecipe, token_usage_dict).
    """
    slug = _generate_recipe_slug(raw_recipe.name)

    normalized_ingredients = _parse_and_normalize_ingredients(raw_recipe)

    system_prompt = _build_enrichment_prompt(raw_recipe, slug)

    llm = _create_llm()
    chain = llm.with_structured_output(StepEnrichmentOutput)

    @llm_retry
    async def _invoke_llm():
        return await chain.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(
                    content=f"Convert the {len(raw_recipe.steps)} flat steps for '{raw_recipe.name}' into structured RecipeStep objects."
                ),
            ]
        )

    result = cast(StepEnrichmentOutput, await _invoke_llm())
    usage = extract_token_usage(result, "enricher")

    linked_steps = _link_steps_to_ingredients(result.steps, normalized_ingredients, raw_recipe)
    linked_steps = _strip_preheat_from_descriptions(linked_steps)
    steps_with_preheat = _inject_preheat_steps(linked_steps, slug)

    enriched = EnrichedRecipe(
        source=raw_recipe,
        steps=steps_with_preheat,
        rag_sources=[],
        chef_notes=result.chef_notes,
        techniques_used=result.techniques_used,
    )
    return enriched, usage


# ── Node function ────────────────────────────────────────────────────────────


async def enrich_recipe_steps_node(state: GRASPState) -> dict:
    """
    LLM-only enrichment node. Processes each raw recipe individually.

    Concurrency: all recipes are enriched concurrently via asyncio.gather().
    Each recipe makes an independent Claude enrichment call, so there is no
    benefit to sequential processing.

    Returns partial GRASPState dict with enriched_recipes (replace semantics).
    Per-recipe failures are recoverable; all-fail is fatal.

    Token usage: accumulated in token_usages and returned as token_usage field.
    GRASPState.token_usage uses operator.add — this list is APPENDED to any
    previously accumulated usage from the generator node.
    """
    raw_recipe_dicts: list[dict] = state.get("raw_recipes", [])

    logger.info("Enriching %d raw recipes", len(raw_recipe_dicts))

    enriched: list[dict] = []
    errors: list[dict] = []
    token_usages: list[dict] = []

    async def _enrich_one(recipe_dict: dict) -> tuple[dict | None, dict | None, dict | None]:
        """Enrich a single recipe, returning (enriched_dict, usage, error).

        Returns (None, None, error_dict) on failure — allows asyncio.gather()
        to collect all results without stopping on the first error.
        """
        recipe_name = recipe_dict.get("name", "unknown")
        try:
            raw_recipe = RawRecipe.model_validate(recipe_dict)
            enriched_recipe, usage = await _enrich_single_recipe(raw_recipe)
            logger.info(
                "Enriched recipe: %s (%d steps)",
                recipe_name,
                len(enriched_recipe.steps),
            )
            return enriched_recipe.model_dump(), usage, None
        except Exception as exc:
            exc_type = type(exc).__name__
            if is_timeout_error(exc):
                error_type = ErrorType.LLM_TIMEOUT
            else:
                error_type = ErrorType.VALIDATION_FAILURE
            logger.warning("Enrichment failed for '%s': %s: %s", recipe_name, exc_type, exc)
            error = NodeError(
                node_name="enricher",
                error_type=error_type,
                recoverable=True,
                message=f"Enrichment failed for '{recipe_name}': {exc_type}: {exc}",
                metadata={"recipe_name": recipe_name, "exception_type": exc_type},
            )
            return None, None, error.model_dump()

    results = await asyncio.gather(*[_enrich_one(rd) for rd in raw_recipe_dicts])

    for enriched_dict, usage, error in results:
        if error:
            errors.append(error)
        else:
            if enriched_dict is not None:
                enriched.append(enriched_dict)
            if usage is not None:
                token_usages.append(usage)

    if not enriched:
        return {
            "enriched_recipes": [],
            "errors": [
                {
                    "node_name": "enricher",
                    "error_type": ErrorType.VALIDATION_FAILURE.value,
                    "recoverable": False,
                    "message": f"All {len(raw_recipe_dicts)} recipes failed enrichment. Cannot proceed.",
                    "metadata": {"failed_count": len(raw_recipe_dicts)},
                }
            ],
        }

    update: dict = {"enriched_recipes": enriched}
    if errors:
        update["errors"] = errors
    if token_usages:
        update["token_usage"] = token_usages
    return update
