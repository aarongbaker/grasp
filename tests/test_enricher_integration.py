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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.enricher import (
    StepEnrichmentOutput,
    _build_enrichment_prompt,
    _format_rag_context,
    _generate_recipe_slug,
    _parse_and_normalize_ingredients,
    _retrieve_rag_context,
    rag_enricher_node,
)
from app.models.enums import ErrorType, MealType, Occasion, Resource
from app.models.pipeline import DinnerConcept
from app.models.recipe import EnrichedRecipe, Ingredient, RawRecipe, RecipeStep

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


def test_build_enrichment_prompt_advisory_contract(sample_raw_recipe):
    """Prompt should explicitly state RAG context is advisory, not canonical."""
    chunks = [
        {"text": "Sear at 200°C for best crust.", "chunk_type": "technique", "chunk_id": "c1"},
    ]
    prompt = _build_enrichment_prompt(sample_raw_recipe, "pan_seared_salmon", chunks)
    
    # Verify advisory-only language is present
    assert "ADVISORY ONLY" in prompt or "advisory" in prompt.lower()
    assert "NOT canonical" in prompt or "not canonical" in prompt.lower()
    
    # Verify it instructs to preserve raw recipe structure
    assert "raw recipe" in prompt.lower() or "RAW RECIPE" in prompt
    
    # Verify it warns against replacing raw recipe with cookbook content
    assert "NEVER replace" in prompt or "never replace" in prompt.lower() or "prioritize the raw recipe" in prompt.lower()


def test_retrieve_rag_context_filters_empty_text():
    """RAG chunks without valid text should be filtered out (advisory contract enforcement)."""
    with (
        patch("openai.OpenAI") as mock_openai,
        patch("pinecone.Pinecone") as mock_pinecone,
        patch("app.graph.nodes.enricher.get_settings") as mock_settings,
    ):
        # Mock settings
        mock_settings.return_value.pinecone_api_key = "test-key"
        mock_settings.return_value.openai_api_key = "test-key"
        mock_settings.return_value.pinecone_index_name = "test-index"
        mock_settings.return_value.rag_retrieval_top_k = 5
        
        # Mock OpenAI embedding response
        mock_openai_instance = MagicMock()
        mock_openai.return_value = mock_openai_instance
        mock_openai_instance.embeddings.create.return_value.data = [
            MagicMock(embedding=[0.1] * 1536)
        ]
        
        # Mock Pinecone query — return chunks with and without valid text
        mock_pc_instance = MagicMock()
        mock_pinecone.return_value = mock_pc_instance
        mock_index = MagicMock()
        mock_pc_instance.Index.return_value = mock_index
        mock_index.query.return_value = {
            "matches": [
                {
                    "id": "chunk1",
                    "metadata": {
                        "text": "Valid cookbook text",
                        "chunk_type": "technique",
                        "chunk_id": "c1",
                    },
                    "score": 0.9,
                },
                {
                    "id": "chunk2",
                    "metadata": {
                        "text": "",  # EMPTY TEXT — should be filtered
                        "chunk_type": "recipe",
                        "chunk_id": "c2",
                    },
                    "score": 0.8,
                },
                {
                    "id": "chunk3",
                    "metadata": {
                        "chunk_type": "narrative",
                        "chunk_id": "c3",
                        # NO TEXT FIELD — should be filtered
                    },
                    "score": 0.7,
                },
                {
                    "id": "chunk4",
                    "metadata": {
                        "text": 12345,  # INVALID TYPE — should be filtered
                        "chunk_type": "tip",
                        "chunk_id": "c4",
                    },
                    "score": 0.6,
                },
                {
                    "id": "chunk5",
                    "metadata": {
                        "text": "Table of contents: soups, salads, desserts",
                        "chunk_type": "index",  # NON-ADVISORY — should be filtered
                        "chunk_id": "c5",
                    },
                    "score": 0.5,
                },
                {
                    "id": "chunk6",
                    "metadata": {
                        "text": "Browse all poultry recipes on pages 10-40",
                        "chunk_type": "catalog",  # NON-ADVISORY — should be filtered
                        "chunk_id": "c6",
                    },
                    "score": 0.4,
                },
            ]
        }
        
        chunks = _retrieve_rag_context("test query", "user123")
        
        # Only chunk1 should survive
        assert len(chunks) == 1
        assert chunks[0]["text"] == "Valid cookbook text"
        assert chunks[0]["chunk_id"] == "c1"


def test_retrieve_rag_context_graceful_degradation_on_missing_api_keys():
    """If API keys are missing, RAG retrieval should return [] without crashing."""
    with patch("app.graph.nodes.enricher.get_settings") as mock_settings:
        mock_settings.return_value.pinecone_api_key = None
        mock_settings.return_value.openai_api_key = "test-key"
        
        chunks = _retrieve_rag_context("test query", "user123")
        assert chunks == []


# ── Unit tests for ingredient parsing and normalization ──────────────────────


def test_parse_and_normalize_ingredients_success():
    """Test successful parsing and normalization for metric and imperial units."""
    from app.graph.nodes.enricher import _parse_and_normalize_ingredients
    
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
    
    # 50g celery → gram (weight)
    celery = result[0]
    assert celery["ingredient_name"] == "celery"
    assert celery["prep_method"] == "diced"
    assert celery["quantity_canonical"] is not None
    assert celery["unit_canonical"] == "gram"
    assert abs(celery["quantity_canonical"] - 50.0) < 0.1
    assert celery["quantity_original"] == "50g"
    assert celery["fallback_reason"] is None
    
    # 2 cups flour → cup (volume)
    flour = result[1]
    assert flour["ingredient_name"] == "flour"
    assert flour["prep_method"] == "sifted"
    assert flour["quantity_canonical"] is not None
    assert flour["unit_canonical"] == "cup"
    assert abs(flour["quantity_canonical"] - 2.0) < 0.1
    assert flour["quantity_original"] == "2 cups"
    assert flour["fallback_reason"] is None
    
    # 1 tablespoon butter → tablespoon (volume)
    butter = result[2]
    assert butter["ingredient_name"] == "butter"
    assert butter["prep_method"] == "melted"
    assert butter["quantity_canonical"] is not None
    assert butter["unit_canonical"] == "tablespoon"
    assert abs(butter["quantity_canonical"] - 1.0) < 0.1
    assert butter["quantity_original"] == "1 tablespoon"
    assert butter["fallback_reason"] is None
    
    # 500 milliliters milk → cup (volume conversion)
    milk = result[3]
    assert milk["ingredient_name"] == "milk"
    assert milk["prep_method"] == "warm"
    assert milk["quantity_canonical"] is not None
    assert milk["unit_canonical"] == "cup"
    # 500 mL ≈ 2.11 cups
    assert abs(milk["quantity_canonical"] - 2.11) < 0.1
    assert milk["quantity_original"] == "500 milliliters"
    assert milk["fallback_reason"] is None


def test_parse_and_normalize_ingredients_fallback():
    """Test fallback behavior for unconvertible units like 'to taste' and 'a pinch'."""
    from app.graph.nodes.enricher import _parse_and_normalize_ingredients
    
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
    
    # "to taste" → fallback with reason
    salt = result[0]
    assert salt["ingredient_name"] == "salt"
    assert salt["quantity_canonical"] is None
    assert salt["unit_canonical"] is None
    assert salt["fallback_reason"] is not None
    assert "no quantity" in salt["fallback_reason"].lower() or "unconvertible" in salt["fallback_reason"].lower()
    
    # "a pinch" → fallback (might parse as quantity but unconvertible unit)
    pepper = result[1]
    assert pepper["ingredient_name"] == "pepper"
    assert pepper["prep_method"] == "freshly ground"
    assert pepper["quantity_canonical"] is None
    assert pepper["unit_canonical"] is None
    assert pepper["fallback_reason"] is not None
    
    # "3" (dimensionless) → fallback
    eggs = result[2]
    assert eggs["ingredient_name"] == "eggs"
    assert eggs["prep_method"] == "beaten"
    assert eggs["quantity_canonical"] is None
    assert eggs["unit_canonical"] is None
    assert eggs["fallback_reason"] is not None
    assert "dimensionless" in eggs["fallback_reason"].lower() or "no quantity" in eggs["fallback_reason"].lower()


def test_parse_and_normalize_ingredients_cross_conversion():
    """Test cross-unit conversions: cups ↔ tbsp, grams ↔ kg."""
    from app.graph.nodes.enricher import _parse_and_normalize_ingredients
    
    raw_recipe = RawRecipe(
        name="Test Recipe",
        description="Test",
        servings=4,
        cuisine="Test",
        estimated_total_minutes=30,
        ingredients=[
            Ingredient(name="water", quantity="16 tablespoons", preparation=""),
            Ingredient(name="oil", quantity="48 teaspoons", preparation=""),
            Ingredient(name="sugar", quantity="2.5 kilograms", preparation=""),
            Ingredient(name="rice", quantity="1500 grams", preparation=""),
        ],
        steps=["Step 1"],
    )
    
    result = _parse_and_normalize_ingredients(raw_recipe)
    
    assert len(result) == 4
    
    # 16 tablespoons water → cup (16 tbsp = 1 cup)
    water = result[0]
    assert water["ingredient_name"] == "water"
    assert water["quantity_canonical"] is not None
    assert water["unit_canonical"] == "cup"
    assert abs(water["quantity_canonical"] - 1.0) < 0.1
    assert water["fallback_reason"] is None
    
    # 48 teaspoons oil → cup (48 tsp = 1 cup)
    oil = result[1]
    assert oil["ingredient_name"] == "oil"
    assert oil["quantity_canonical"] is not None
    assert oil["unit_canonical"] == "cup"
    assert abs(oil["quantity_canonical"] - 1.0) < 0.1
    assert oil["fallback_reason"] is None
    
    # 2.5 kilograms sugar → gram (2.5 kg = 2500 g)
    sugar = result[2]
    assert sugar["ingredient_name"] == "sugar"
    assert sugar["quantity_canonical"] is not None
    assert sugar["unit_canonical"] == "gram"
    assert abs(sugar["quantity_canonical"] - 2500.0) < 0.1
    assert sugar["fallback_reason"] is None
    
    # 1500 grams rice → gram (already in canonical weight)
    rice = result[3]
    assert rice["ingredient_name"] == "rice"
    assert rice["quantity_canonical"] is not None
    assert rice["unit_canonical"] == "gram"
    assert abs(rice["quantity_canonical"] - 1500.0) < 0.1
    assert rice["fallback_reason"] is None


def test_parse_and_normalize_ingredients_malformed_empty_name():
    """Test malformed input: empty ingredient name."""
    from app.graph.nodes.enricher import _parse_and_normalize_ingredients
    
    raw_recipe = RawRecipe(
        name="Test Recipe",
        description="Test",
        servings=4,
        cuisine="Test",
        estimated_total_minutes=30,
        ingredients=[
            Ingredient(name="", quantity="100g", preparation=""),
        ],
        steps=["Step 1"],
    )
    
    result = _parse_and_normalize_ingredients(raw_recipe)
    
    assert len(result) == 1
    # Should handle gracefully, even if parser struggles
    ing = result[0]
    assert ing["quantity_original"] == "100g"
    # Either parsed successfully or fallback reason present
    assert ing["fallback_reason"] is not None or ing["quantity_canonical"] is not None


def test_parse_and_normalize_ingredients_missing_quantity():
    """Test malformed input: missing quantity → fallback with reason."""
    from app.graph.nodes.enricher import _parse_and_normalize_ingredients
    
    raw_recipe = RawRecipe(
        name="Test Recipe",
        description="Test",
        servings=4,
        cuisine="Test",
        estimated_total_minutes=30,
        ingredients=[
            Ingredient(name="vanilla extract", quantity="", preparation=""),
        ],
        steps=["Step 1"],
    )
    
    result = _parse_and_normalize_ingredients(raw_recipe)
    
    assert len(result) == 1
    vanilla = result[0]
    assert vanilla["ingredient_name"] == "vanilla extract"
    assert vanilla["quantity_canonical"] is None
    assert vanilla["unit_canonical"] is None
    assert vanilla["fallback_reason"] is not None
    assert "no quantity" in vanilla["fallback_reason"].lower()


def test_parse_and_normalize_ingredients_zero_quantity():
    """Test boundary condition: 0 cups."""
    from app.graph.nodes.enricher import _parse_and_normalize_ingredients
    
    raw_recipe = RawRecipe(
        name="Test Recipe",
        description="Test",
        servings=4,
        cuisine="Test",
        estimated_total_minutes=30,
        ingredients=[
            Ingredient(name="water", quantity="0 cups", preparation=""),
        ],
        steps=["Step 1"],
    )
    
    result = _parse_and_normalize_ingredients(raw_recipe)
    
    assert len(result) == 1
    water = result[0]
    # Parser might handle 0, or might fallback - either is acceptable
    # Key: function doesn't crash
    assert water["ingredient_name"] == "water"
    assert water["quantity_original"] == "0 cups"


def test_parse_and_normalize_ingredients_large_quantity():
    """Test boundary condition: very large quantities."""
    from app.graph.nodes.enricher import _parse_and_normalize_ingredients
    
    raw_recipe = RawRecipe(
        name="Test Recipe",
        description="Test",
        servings=100,
        cuisine="Test",
        estimated_total_minutes=30,
        ingredients=[
            Ingredient(name="flour", quantity="50 kilograms", preparation=""),
        ],
        steps=["Step 1"],
    )
    
    result = _parse_and_normalize_ingredients(raw_recipe)
    
    assert len(result) == 1
    flour = result[0]
    assert flour["ingredient_name"] == "flour"
    assert flour["quantity_canonical"] is not None
    assert flour["unit_canonical"] == "gram"
    # 50 kg = 50000 g
    assert abs(flour["quantity_canonical"] - 50000.0) < 1.0
    assert flour["fallback_reason"] is None


def test_parse_and_normalize_ingredients_unicode_names():
    """Test boundary condition: Unicode ingredient names."""
    from app.graph.nodes.enricher import _parse_and_normalize_ingredients
    
    raw_recipe = RawRecipe(
        name="Test Recipe",
        description="Test",
        servings=4,
        cuisine="Test",
        estimated_total_minutes=30,
        ingredients=[
            Ingredient(name="crème fraîche", quantity="200g", preparation=""),
            Ingredient(name="jalapeño", quantity="1 tablespoon", preparation="diced"),
        ],
        steps=["Step 1"],
    )
    
    result = _parse_and_normalize_ingredients(raw_recipe)
    
    assert len(result) == 2
    # Should handle Unicode gracefully
    creme = result[0]
    assert "cr" in creme["ingredient_name"].lower()
    assert creme["quantity_canonical"] is not None
    
    jalapeno = result[1]
    assert "jalape" in jalapeno["ingredient_name"].lower()
    assert jalapeno["quantity_canonical"] is not None


# ── Unit tests for node error handling (mocked LLM) ─────────────────────────


@pytest.mark.asyncio
async def test_enricher_per_recipe_error_keeps_survivors():
    """If one recipe fails enrichment, the others should still be returned."""
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

    with (
        patch("app.graph.nodes.enricher._create_llm", return_value=mock_llm),
        patch("app.graph.nodes.enricher._retrieve_rag_context", return_value=[]),
    ):
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

    with (
        patch("app.graph.nodes.enricher._create_llm", return_value=mock_llm),
        patch("app.graph.nodes.enricher._retrieve_rag_context", return_value=[]),
    ):
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


@pytest.mark.asyncio
async def test_enricher_preserves_raw_recipe_structure_not_rag():
    """
    Verify enricher uses raw_recipe steps as authoritative structure, not RAG chunks.
    Even if RAG chunks contain recipe-like text, enricher should generate steps from
    raw_recipe and use RAG only as advisory context.
    """
    from tests.fixtures.recipes import ENRICHED_SHORT_RIBS, RAW_SHORT_RIBS

    # Mock RAG to return recipe-like text that SHOULD NOT be used as structure
    fake_rag_chunks = [
        {
            "text": "1. Brown the meat. 2. Add wine. 3. Braise for 3 hours.",
            "chunk_type": "recipe",
            "chunk_id": "fake1",
        }
    ]

    async def mock_llm_response(messages):
        # LLM should see RAG context but generate steps from raw_recipe
        return StepEnrichmentOutput(
            steps=ENRICHED_SHORT_RIBS.steps,
            chef_notes="Used cookbook advice for timing.",
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

    with (
        patch("app.graph.nodes.enricher._create_llm", return_value=mock_llm),
        patch("app.graph.nodes.enricher._retrieve_rag_context", return_value=fake_rag_chunks),
    ):
        result = await rag_enricher_node(state)

    # Should have one enriched recipe
    assert len(result["enriched_recipes"]) == 1
    enriched = result["enriched_recipes"][0]
    
    # Recipe name should come from raw_recipe, not RAG
    assert enriched["source"]["name"] == RAW_SHORT_RIBS.name
    
    # Number of steps should match raw_recipe, not RAG text
    assert len(enriched["steps"]) == len(RAW_SHORT_RIBS.steps)
    
    # RAG sources should be recorded but not used as recipe structure
    assert len(enriched["rag_sources"]) == 1
    assert enriched["rag_sources"][0] == "fake1"

    # Menu-intent source remains authoritative; advisory cookbook text does not rewrite it
    assert enriched["source"]["description"] == RAW_SHORT_RIBS.description
    assert enriched["source"]["steps"] == RAW_SHORT_RIBS.steps


# ── Integration tests (real Claude API) ──────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_enricher_node_produces_valid_enriched_recipes(sample_state):
    """Call the real enricher node and validate output structure."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(SKIP_REASON)

    with patch("app.graph.nodes.enricher._retrieve_rag_context", return_value=[]):
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
            assert dep in all_ids, f"Step '{step.step_id}' depends on '{dep}' which is not in step list"

    # Chef notes and techniques should be populated
    assert enriched.chef_notes, "chef_notes should not be empty"
    assert len(enriched.techniques_used) > 0, "techniques_used should not be empty"

    print(f"\nEnriched '{enriched.source.name}' into {len(enriched.steps)} structured steps:")
    for s in enriched.steps:
        print(f"  {s.step_id}: {s.resource.value} {s.duration_minutes}min — {s.description[:60]}")
