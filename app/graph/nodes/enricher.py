"""
graph/nodes/enricher.py
Real RAG enricher — Phase 5. Second LLM call in the system.

Reads raw_recipes from GRASPState, queries Pinecone for relevant cookbook
chunks (per-chef isolation via user_id), then calls Claude to convert flat
step strings into structured RecipeStep objects with timing, resource tags,
and dependency edges.

Error handling: per-recipe recoverable. If one recipe fails enrichment,
it is dropped and the pipeline continues with survivors. If ALL recipes
fail, the error is fatal (recoverable=False) — nothing to validate or
schedule.

RAG graceful degradation: if Pinecone query fails or returns zero results,
enrichment proceeds with LLM-only (no RAG context). rag_sources is set to [].
The pipeline never fails due to missing cookbooks.

IDEMPOTENCY: Returns enriched_recipes as a NEW list (not appended).
Replace semantics — same contract as generator (§2.10).

Mockable seams:
  _create_llm()            — returns ChatAnthropic instance
  _retrieve_rag_context()  — embeds query + queries Pinecone
Tests patch these two functions to bypass external APIs.
"""

import asyncio
import logging
import re

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


# ── Structured output wrapper ────────────────────────────────────────────────


class StepEnrichmentOutput(BaseModel):
    """Wrapper for LangChain structured output. Claude returns this shape."""

    steps: list[RecipeStep]
    chef_notes: str
    techniques_used: list[str]


# ── Slug generation ──────────────────────────────────────────────────────────


def _generate_recipe_slug(name: str) -> str:
    """
    Convert recipe name to a URL-safe slug for step_id generation.
    'Braised Short Ribs' → 'braised_short_ribs'
    """
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ── RAG retrieval (mockable seam) ────────────────────────────────────────────


def _retrieve_rag_context(query: str, user_id: str, rag_owner_key: str = "") -> list[dict]:
    """
    Embed query with OpenAI and search Pinecone for relevant cookbook chunks.
    Returns list of chunk dicts with 'text', 'chunk_type', 'chunk_id' keys.

    Graceful degradation: returns [] on any failure (network, auth, empty index).
    """
    try:
        from openai import OpenAI
        from pinecone import Pinecone

        settings = get_settings()

        if not settings.pinecone_api_key or not settings.openai_api_key:
            return []

        # Embed the query
        openai_client = OpenAI(api_key=settings.openai_api_key)
        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=[query],
        )
        query_embedding = response.data[0].embedding

        # Query Pinecone with user_id filter
        pc = Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(settings.pinecone_index_name)

        owner_filter = {}
        if rag_owner_key:
            owner_filter = {"rag_owner_key": {"$eq": rag_owner_key}}
        elif user_id:
            owner_filter = {"user_id": {"$eq": user_id}}

        results = index.query(
            vector=query_embedding,
            top_k=settings.rag_retrieval_top_k,
            filter=owner_filter,
            include_metadata=True,
        )

        chunks = []
        for match in results.get("matches", []):
            metadata = match.get("metadata", {})
            chunks.append(
                {
                    "text": metadata.get("text", ""),
                    "chunk_type": metadata.get("chunk_type", ""),
                    "chunk_id": metadata.get("chunk_id", match.get("id", "")),
                    "score": match.get("score", 0.0),
                }
            )

        return chunks

    except Exception as exc:
        logger.warning("RAG retrieval failed (graceful degradation): %s", exc)
        return []


# ── Prompt builders ──────────────────────────────────────────────────────────


def _format_rag_context(chunks: list[dict]) -> str:
    """Format retrieved RAG chunks for inclusion in the enrichment prompt."""
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
    rag_context: list[dict],
) -> str:
    """Build the system prompt for step enrichment."""
    rag_text = _format_rag_context(rag_context)

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

## CHEF'S COOKBOOK CONTEXT
{rag_text}

## OUTPUT REQUIREMENTS
1. Generate exactly {len(raw_recipe.steps)} RecipeStep objects, one per flat text step.
2. Each step description should be a refined, actionable version of the flat text — add precision (temperatures in °C, visual cues, timing details) but preserve the original intent.
3. chef_notes: 1-2 sentences of practical advice for executing this recipe. Incorporate RAG context if available.
4. techniques_used: list of culinary techniques employed (e.g. "braising", "emulsification", "tempering")."""


# ── LLM factory (mockable seam) ─────────────────────────────────────────────


def _create_llm() -> ChatAnthropic:
    """
    Creates the ChatAnthropic instance. Extracted as a separate function so
    tests can patch graph.nodes.enricher._create_llm to bypass the real API.
    """
    settings = get_settings()
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        api_key=settings.anthropic_api_key,
        max_tokens=4096,
    )


# ── Per-recipe enrichment ────────────────────────────────────────────────────


async def _enrich_single_recipe(
    raw_recipe: RawRecipe,
    user_id: str,
    rag_owner_key: str,
) -> tuple[EnrichedRecipe, dict]:
    """
    Enrich a single RawRecipe: RAG retrieval + LLM structured output.
    Raises on failure — caller handles per-recipe error isolation.
    Returns (EnrichedRecipe, token_usage_dict).
    """
    slug = _generate_recipe_slug(raw_recipe.name)

    # RAG retrieval (graceful degradation — returns [] on failure)
    rag_query = f"{raw_recipe.name} {raw_recipe.cuisine} {raw_recipe.description}"
    rag_chunks = await asyncio.to_thread(_retrieve_rag_context, rag_query, user_id, rag_owner_key)

    # Build prompt
    system_prompt = _build_enrichment_prompt(raw_recipe, slug, rag_chunks)

    # Call Claude with structured output (with retry on transient errors)
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

    result = await _invoke_llm()
    usage = extract_token_usage(result, "rag_enricher")

    # Build EnrichedRecipe
    rag_source_ids = [c.get("chunk_id", "") for c in rag_chunks if c.get("chunk_id")]

    enriched = EnrichedRecipe(
        source=raw_recipe,
        steps=result.steps,
        rag_sources=rag_source_ids,
        chef_notes=result.chef_notes,
        techniques_used=result.techniques_used,
    )
    return enriched, usage


# ── Node function ────────────────────────────────────────────────────────────


async def rag_enricher_node(state: GRASPState) -> dict:
    """
    Real RAG enricher node. Processes each raw recipe individually.

    Returns partial GRASPState dict with enriched_recipes (replace semantics).
    Per-recipe failures are recoverable; all-fail is fatal.
    """
    raw_recipe_dicts: list[dict] = state.get("raw_recipes", [])
    user_id: str = state.get("user_id", "")
    rag_owner_key: str = state.get("rag_owner_key", "")

    logger.info("Enriching %d raw recipes", len(raw_recipe_dicts))

    enriched: list[dict] = []
    errors: list[dict] = []
    token_usages: list[dict] = []

    async def _enrich_one(recipe_dict: dict) -> tuple[dict | None, dict | None, dict | None]:
        """Enrich a single recipe, returning (enriched_dict, usage, error)."""
        recipe_name = recipe_dict.get("name", "unknown")
        try:
            raw_recipe = RawRecipe.model_validate(recipe_dict)
            enriched_recipe, usage = await _enrich_single_recipe(raw_recipe, user_id, rag_owner_key)
            logger.info(
                "Enriched recipe: %s (%d steps, %d RAG sources)",
                recipe_name,
                len(enriched_recipe.steps),
                len(enriched_recipe.rag_sources),
            )
            return enriched_recipe.model_dump(), usage, None
        except Exception as exc:
            exc_type = type(exc).__name__
            if is_timeout_error(exc):
                error_type = ErrorType.LLM_TIMEOUT
            else:
                error_type = ErrorType.RAG_FAILURE
            logger.warning("Enrichment failed for '%s': %s: %s", recipe_name, exc_type, exc)
            error = NodeError(
                node_name="rag_enricher",
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
            enriched.append(enriched_dict)
            token_usages.append(usage)

    if not enriched:
        # All recipes failed — fatal
        return {
            "enriched_recipes": [],
            "errors": [
                {
                    "node_name": "rag_enricher",
                    "error_type": ErrorType.RAG_FAILURE.value,
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
