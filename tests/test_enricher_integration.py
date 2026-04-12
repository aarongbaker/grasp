"""
tests/test_enricher_integration.py
Unit tests for the active LLM-only enricher helper and node seams.

Integration tests use the real Anthropic client and are skipped if
ANTHROPIC_API_KEY is not set.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.enricher import (
    StepEnrichmentOutput,
    _build_enrichment_context,
    _build_enrichment_prompt,
    _generate_recipe_slug,
    _parse_and_normalize_ingredients,
    enrich_recipe_steps_node,
)
from app.models.enums import ErrorType, MealType, Occasion, Resource
from app.models.pipeline import DinnerConcept
from app.models.recipe import Ingredient, RawRecipe, RecipeStep

SKIP_REASON = "ANTHROPIC_API_KEY not set — skipping integration test"


@pytest.fixture
def sample_raw_recipe() -> RawRecipe:
    return RawRecipe(
        name="Pan-Seared Salmon",
        description="Crispy-skinned salmon with lemon butter sauce.",
        servings=2,
        cuisine="French",
        estimated_total_minutes=30,
        ingredients=[
            Ingredient(name="salmon fillets", quantity="2 x 200g", preparation="skin-on, pat dry"),
            Ingredient(name="butter", quantity="30g"),
            Ingredient(name="lemon", quantity="1", preparation="juiced"),
            Ingredient(name="salt and pepper", quantity="to taste"),
        ],
        steps=[
            "Season salmon fillets with salt and pepper. Heat oil in a non-stick pan over high heat.",
            "Place salmon skin-side down. Press gently. Cook for 4 minutes until skin is crispy.",
            "Flip and cook 2 more minutes. Remove to a warm plate. Rest for 2 minutes.",
            "Add butter to the pan. When foaming, add lemon juice. Swirl to emulsify. Pour over salmon.",
        ],
    )


@pytest.fixture
def sample_state(sample_raw_recipe) -> dict:
    concept = DinnerConcept(
        free_text="A simple French dinner with salmon.",
        guest_count=2,
        meal_type=MealType.DINNER,
        occasion=Occasion.CASUAL,
        dietary_restrictions=[],
    )
    return {
        "concept": concept.model_dump(),
        "kitchen_config": {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        "equipment": [],
        "user_id": "test-user-123",
        "raw_recipes": [sample_raw_recipe.model_dump()],
        "errors": [],
    }


def test_generate_recipe_slug():
    assert _generate_recipe_slug("Braised Short Ribs") == "braised_short_ribs"
    assert _generate_recipe_slug("Pommes Puree") == "pommes_puree"
    assert _generate_recipe_slug("Chocolate Fondant") == "chocolate_fondant"
    assert _generate_recipe_slug("Pan-Seared Salmon") == "pan_seared_salmon"
    assert _generate_recipe_slug("  Crème Brûlée! ") == "cr_me_br_l_e"


def test_build_enrichment_context_is_llm_only():
    context = _build_enrichment_context()
    assert "No external cookbook context" in context
    assert "general culinary knowledge" in context


def test_build_enrichment_prompt_includes_recipe(sample_raw_recipe):
    prompt = _build_enrichment_prompt(sample_raw_recipe, "pan_seared_salmon")
    assert "Pan-Seared Salmon" in prompt
    assert "salmon fillets" in prompt
    assert "pan_seared_salmon_step_" in prompt
    assert "Season salmon" in prompt


def test_build_enrichment_prompt_names_llm_only_contract(sample_raw_recipe):
    prompt = _build_enrichment_prompt(sample_raw_recipe, "pan_seared_salmon")
    assert "The active product path does not retrieve cookbook-specific context during enrichment" in prompt
    assert "Use the raw recipe and your general culinary knowledge only" in prompt
    assert "ADVISORY COOKBOOK CONTEXT" not in prompt


def test_build_enrichment_prompt_resource_types(sample_raw_recipe):
    prompt = _build_enrichment_prompt(sample_raw_recipe, "pan_seared_salmon")
    assert "oven" in prompt.lower()
    assert "stovetop" in prompt.lower()
    assert "passive" in prompt.lower()
    assert "hands" in prompt.lower()


def test_parse_and_normalize_ingredients_success():
    raw_recipe = RawRecipe(
        name="Test Recipe",
        description="Test",
        servings=4,
        cuisine="Test",
        estimated_total_minutes=30,
        ingredients=[
            Ingredient(name="celery", quantity="50g", preparation="diced"),
            Ingredient(name="flour", quantity="2 cups", preparation="sifted"),
            Ingredient(name="butter", quantity="1 tablespoon", preparation="melted"),
            Ingredient(name="milk", quantity="500 milliliters", preparation="warm"),
        ],
        steps=["Step 1"],
    )

    result = _parse_and_normalize_ingredients(raw_recipe)

    assert len(result) == 4
    assert result[0]["unit_canonical"] == "gram"
    assert result[1]["unit_canonical"] == "cup"
    assert result[2]["unit_canonical"] == "tablespoon"
    assert result[3]["unit_canonical"] == "cup"


def test_parse_and_normalize_ingredients_fallback():
    raw_recipe = RawRecipe(
        name="Test Recipe",
        description="Test",
        servings=4,
        cuisine="Test",
        estimated_total_minutes=30,
        ingredients=[
            Ingredient(name="salt", quantity="to taste", preparation=""),
            Ingredient(name="pepper", quantity="a pinch", preparation="freshly ground"),
            Ingredient(name="eggs", quantity="3", preparation="beaten"),
        ],
        steps=["Step 1"],
    )

    result = _parse_and_normalize_ingredients(raw_recipe)

    assert len(result) == 3
    assert result[0]["quantity_canonical"] is None
    assert result[1]["quantity_canonical"] is None
    assert result[2]["quantity_canonical"] is None
    assert result[2]["fallback_reason"] is not None


@pytest.mark.asyncio
async def test_enricher_per_recipe_error_keeps_survivors():
    from tests.fixtures.recipes import ENRICHED_SHORT_RIBS, RAW_POMMES_PUREE, RAW_SHORT_RIBS

    call_count = 0

    async def side_effect(messages):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("Simulated LLM failure for recipe 2")
        return StepEnrichmentOutput(
            steps=ENRICHED_SHORT_RIBS.steps,
            chef_notes="Test notes",
            techniques_used=["searing"],
        )

    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(side_effect=side_effect)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain

    state = {
        "raw_recipes": [RAW_SHORT_RIBS.model_dump(), RAW_POMMES_PUREE.model_dump()],
        "user_id": "",
        "errors": [],
    }

    with patch("app.graph.nodes.enricher._create_llm", return_value=mock_llm):
        result = await enrich_recipe_steps_node(state)

    assert len(result["enriched_recipes"]) == 1
    assert "errors" in result
    assert len(result["errors"]) == 1
    assert result["errors"][0]["recoverable"] is True


@pytest.mark.asyncio
async def test_enricher_all_fail_is_fatal():
    from tests.fixtures.recipes import RAW_SHORT_RIBS

    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(side_effect=Exception("LLM unavailable"))
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain

    state = {
        "raw_recipes": [RAW_SHORT_RIBS.model_dump()],
        "user_id": "",
        "errors": [],
    }

    with patch("app.graph.nodes.enricher._create_llm", return_value=mock_llm):
        result = await enrich_recipe_steps_node(state)

    assert result["enriched_recipes"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["recoverable"] is False
    assert result["errors"][0]["error_type"] == ErrorType.VALIDATION_FAILURE.value
    assert result["errors"][0]["node_name"] == "enricher"


@pytest.mark.asyncio
async def test_enricher_empty_raw_recipes():
    state = {"raw_recipes": [], "user_id": "", "errors": []}

    result = await enrich_recipe_steps_node(state)

    assert result["enriched_recipes"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["recoverable"] is False


@pytest.mark.asyncio
async def test_enricher_preserves_raw_recipe_structure_with_empty_rag_sources():
    from tests.fixtures.recipes import ENRICHED_SHORT_RIBS, RAW_SHORT_RIBS

    async def mock_llm_response(messages):
        return StepEnrichmentOutput(
            steps=ENRICHED_SHORT_RIBS.steps,
            chef_notes="Used general culinary knowledge for timing.",
            techniques_used=["searing", "braising"],
        )

    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(side_effect=mock_llm_response)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain

    state = {
        "raw_recipes": [RAW_SHORT_RIBS.model_dump()],
        "user_id": "test-user",
        "errors": [],
    }

    with patch("app.graph.nodes.enricher._create_llm", return_value=mock_llm):
        result = await enrich_recipe_steps_node(state)

    assert len(result["enriched_recipes"]) == 1
    enriched = result["enriched_recipes"][0]
    assert enriched["source"]["name"] == RAW_SHORT_RIBS.name
    assert len(enriched["steps"]) == len(RAW_SHORT_RIBS.steps)
    assert enriched["rag_sources"] == []
    assert enriched["source"]["description"] == RAW_SHORT_RIBS.description


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_enricher_node_returns_enriched_recipe(sample_state):
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip(SKIP_REASON)

    result = await enrich_recipe_steps_node(sample_state)

    assert "enriched_recipes" in result
    assert len(result["enriched_recipes"]) == 1

    enriched = result["enriched_recipes"][0]
    model = EnrichedRecipe.model_validate(enriched)

    assert model.source.name == "Pan-Seared Salmon"
    assert len(model.steps) >= 4
    assert all(step.duration_minutes > 0 for step in model.steps)
    assert all(step.resource in {Resource.OVEN, Resource.STOVETOP, Resource.PASSIVE, Resource.HANDS} for step in model.steps)
    assert isinstance(model.chef_notes, str)
    assert model.rag_sources == []

    if "token_usage" in result:
        assert len(result["token_usage"]) >= 1
        assert result["token_usage"][0]["node"] == "enricher"
