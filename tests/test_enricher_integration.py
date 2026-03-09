"""
tests/test_enricher_integration.py
Unit tests for enricher helpers (no API calls) and integration tests
for the real enricher node (real Claude + Pinecone).

Unit tests run in the normal test suite. Integration tests are marked
@pytest.mark.integration and skipped if ANTHROPIC_API_KEY is not set.

Run separately:
    pytest tests/test_enricher_integration.py -v -m "not integration"  # unit only
    pytest tests/test_enricher_integration.py -v                       # all
"""

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from models.pipeline import DinnerConcept
from models.enums import MealType, Occasion, Resource, ErrorType
from models.recipe import RawRecipe, Ingredient, RecipeStep, EnrichedRecipe
from graph.nodes.enricher import (
    rag_enricher_node,
    StepEnrichmentOutput,
    _generate_recipe_slug,
    _build_enrichment_prompt,
    _format_rag_context,
)


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


# ── Unit tests for helpers (no API call) ─────────────────────────────────────

def test_generate_recipe_slug():
    """Verify slug generation from recipe names."""
    assert _generate_recipe_slug("Braised Short Ribs") == "braised_short_ribs"
    assert _generate_recipe_slug("Pommes Puree") == "pommes_puree"
    assert _generate_recipe_slug("Chocolate Fondant") == "chocolate_fondant"
    assert _generate_recipe_slug("Pan-Seared Salmon") == "pan_seared_salmon"
    assert _generate_recipe_slug("  Crème Brûlée! ") == "cr_me_br_l_e"


def test_format_rag_context_empty():
    """Empty chunks should return fallback text."""
    result = _format_rag_context([])
    assert "No cookbook-specific context" in result


def test_format_rag_context_with_chunks():
    """RAG chunks should be formatted with type labels."""
    chunks = [
        {"text": "Sear at high heat for best crust.", "chunk_type": "technique", "chunk_id": "c1"},
        {"text": "Rest meat 10-15 min after cooking.", "chunk_type": "tip", "chunk_id": "c2"},
    ]
    result = _format_rag_context(chunks)
    assert "[TECHNIQUE #1]" in result
    assert "Sear at high heat" in result
    assert "[TIP #2]" in result
    assert "Rest meat" in result


def test_build_enrichment_prompt_includes_recipe(sample_raw_recipe):
    """Prompt should include recipe name, ingredients, and steps."""
    prompt = _build_enrichment_prompt(sample_raw_recipe, "pan_seared_salmon", [])
    assert "Pan-Seared Salmon" in prompt
    assert "salmon fillets" in prompt
    assert "pan_seared_salmon_step_" in prompt
    assert "Season salmon" in prompt


def test_build_enrichment_prompt_includes_rag_context(sample_raw_recipe):
    """RAG context should appear in the prompt when chunks are provided."""
    chunks = [
        {"text": "For crispy skin, start skin-side down on high heat.", "chunk_type": "technique", "chunk_id": "c1"},
    ]
    prompt = _build_enrichment_prompt(sample_raw_recipe, "pan_seared_salmon", chunks)
    assert "crispy skin" in prompt
    assert "[TECHNIQUE #1]" in prompt


def test_build_enrichment_prompt_resource_types(sample_raw_recipe):
    """Prompt should explain all 4 resource types."""
    prompt = _build_enrichment_prompt(sample_raw_recipe, "pan_seared_salmon", [])
    assert "oven" in prompt.lower()
    assert "stovetop" in prompt.lower()
    assert "passive" in prompt.lower()
    assert "hands" in prompt.lower()


# ── Unit tests for node error handling (mocked LLM) ─────────────────────────

@pytest.mark.asyncio
async def test_enricher_per_recipe_error_keeps_survivors():
    """If one recipe fails enrichment, the others should still be returned."""
    from tests.fixtures.recipes import RAW_SHORT_RIBS, RAW_POMMES_PUREE, ENRICHED_SHORT_RIBS

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

    with patch("graph.nodes.enricher._create_llm", return_value=mock_llm), \
         patch("graph.nodes.enricher._retrieve_rag_context", return_value=[]):
        result = await rag_enricher_node(state)

    # 1 enriched recipe survived, 1 error
    assert len(result["enriched_recipes"]) == 1
    assert "errors" in result
    assert len(result["errors"]) == 1
    assert result["errors"][0]["recoverable"] is True


@pytest.mark.asyncio
async def test_enricher_all_fail_is_fatal():
    """If all recipes fail enrichment, the error should be fatal."""
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

    with patch("graph.nodes.enricher._create_llm", return_value=mock_llm), \
         patch("graph.nodes.enricher._retrieve_rag_context", return_value=[]):
        result = await rag_enricher_node(state)

    assert result["enriched_recipes"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["recoverable"] is False
    assert result["errors"][0]["error_type"] == ErrorType.RAG_FAILURE.value


@pytest.mark.asyncio
async def test_enricher_empty_raw_recipes():
    """If raw_recipes is empty, enricher should return fatal error."""
    state = {"raw_recipes": [], "user_id": "", "errors": []}

    result = await rag_enricher_node(state)

    assert result["enriched_recipes"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["recoverable"] is False


# ── Integration tests (real Claude API) ──────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_enricher_node_produces_valid_enriched_recipes(sample_state):
    """Call the real enricher node and validate output structure."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(SKIP_REASON)

    with patch("graph.nodes.enricher._retrieve_rag_context", return_value=[]):
        result = await rag_enricher_node(sample_state)

    # Should not have errors
    assert "errors" not in result or len(result.get("errors", [])) == 0, (
        f"Enricher returned errors: {result.get('errors')}"
    )

    enriched_recipes = result["enriched_recipes"]
    assert len(enriched_recipes) == 1

    # Validate against EnrichedRecipe schema
    enriched = EnrichedRecipe.model_validate(enriched_recipes[0])

    # Source should be preserved
    assert enriched.source.name == "Pan-Seared Salmon"

    # Steps should be structured RecipeStep objects
    assert len(enriched.steps) == 4, f"Expected 4 steps, got {len(enriched.steps)}"

    for step in enriched.steps:
        assert step.step_id.startswith("pan_seared_salmon_step_")
        assert step.duration_minutes > 0
        assert step.resource in Resource
        assert step.description, "Step description must be non-empty"

    # Step IDs should be sequential
    step_ids = [s.step_id for s in enriched.steps]
    for i, sid in enumerate(step_ids, 1):
        assert sid == f"pan_seared_salmon_step_{i}", f"Step {i} has wrong ID: {sid}"

    # depends_on references should all be valid step IDs
    all_ids = set(step_ids)
    for step in enriched.steps:
        for dep in step.depends_on:
            assert dep in all_ids, (
                f"Step '{step.step_id}' depends on '{dep}' which is not in step list"
            )

    # Chef notes and techniques should be populated
    assert enriched.chef_notes, "chef_notes should not be empty"
    assert len(enriched.techniques_used) > 0, "techniques_used should not be empty"

    print(f"\nEnriched '{enriched.source.name}' into {len(enriched.steps)} structured steps:")
    for s in enriched.steps:
        print(f"  {s.step_id}: {s.resource.value} {s.duration_minutes}min — {s.description[:60]}")
