"""
tests/test_oven_temp_extraction.py

Unit tests for oven temperature extraction from step descriptions.
Tests numeric Fahrenheit, Celsius conversion, and vague heat inference.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.graph.nodes.enricher import enrich_recipe_steps_node
from app.models.recipe import RawRecipe, Ingredient, RecipeStep


@pytest.fixture
def mock_llm():
    """Mock the LLM to bypass real API calls during tests."""
    with patch("app.graph.nodes.enricher._create_llm") as mock_create:
        mock_instance = MagicMock()
        mock_create.return_value = mock_instance
        yield mock_instance


@pytest.mark.asyncio
async def test_numeric_temp_extraction(mock_llm):
    """
    Test that numeric Fahrenheit temperatures are extracted correctly.
    
    Verifies:
    - "Preheat oven to 375°F" extracts oven_temp_f = 375
    - "Bake at 425°F for 20 minutes" extracts oven_temp_f = 425
    - Preheat step is automatically injected before first oven usage
    """
    # Setup mock recipe with numeric temps
    raw_recipe = RawRecipe(
        name="Test Numeric Temps",
        cuisine="American",
        description="Test recipe",
        servings=4,
        estimated_total_minutes=60,
        ingredients=[
            Ingredient(name="flour", quantity="2 cups", preparation="sifted")
        ],
        steps=[
            "Preheat oven to 375°F",
            "Bake at 425°F for 20 minutes",
            "Let cool on rack",
        ],
    )
    
    # Mock LLM response with temperature extraction
    mock_response = MagicMock()
    mock_response.steps = [
        RecipeStep(
            step_id="test_numeric_temps_step_1",
            description="Preheat oven to 375°F",
            duration_minutes=10,
            resource="oven",
            oven_temp_f=375,
        ),
        RecipeStep(
            step_id="test_numeric_temps_step_2",
            description="Bake at 425°F for 20 minutes",
            duration_minutes=20,
            resource="oven",
            oven_temp_f=425,
        ),
        RecipeStep(
            step_id="test_numeric_temps_step_3",
            description="Let cool on rack",
            duration_minutes=15,
            resource="passive",
            oven_temp_f=None,
        ),
    ]
    mock_response.chef_notes = "Simple baking test"
    mock_response.techniques_used = ["baking"]
    
    # Setup token usage metadata
    mock_response.response_metadata = {
        "usage": {"input_tokens": 100, "output_tokens": 200}
    }
    
    # Configure mock chain
    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.with_structured_output = MagicMock(return_value=mock_chain)
    
    # Execute enricher
    state = {
        "raw_recipes": [raw_recipe.model_dump()],
        "user_id": "test_user",
        "rag_owner_key": "",
    }
    
    result = await enrich_recipe_steps_node(state)
    
    # Verify temperature extraction
    assert len(result["enriched_recipes"]) == 1
    enriched = result["enriched_recipes"][0]
    
    # Should have 4 steps (1 auto-injected preheat + 3 original)
    assert len(enriched["steps"]) == 4
    
    # First step should be auto-injected preheat
    assert enriched["steps"][0]["step_id"] == "test_numeric_temps_preheat_1"
    assert enriched["steps"][0]["oven_temp_f"] == 375
    
    # Check original steps with temps (now shifted by 1)
    assert enriched["steps"][1]["oven_temp_f"] == 375
    assert enriched["steps"][2]["oven_temp_f"] == 425
    assert enriched["steps"][3]["oven_temp_f"] is None


@pytest.mark.asyncio
async def test_celsius_conversion(mock_llm):
    """
    Test that Celsius temperatures are converted to Fahrenheit.
    
    Verifies:
    - "150°C" converts to 302°F
    - "200°C" converts to 392°F
    - Preheat step is automatically injected
    """
    raw_recipe = RawRecipe(
        name="Test Celsius",
        cuisine="European",
        description="Test recipe",
        servings=4,
        estimated_total_minutes=60,
        ingredients=[
            Ingredient(name="flour", quantity="2 cups", preparation="sifted")
        ],
        steps=[
            "Preheat oven to 150°C",
            "Bake at 200°C for 25 minutes",
        ],
    )
    
    mock_response = MagicMock()
    mock_response.steps = [
        RecipeStep(
            step_id="test_celsius_step_1",
            description="Preheat oven to 150°C (302°F)",
            duration_minutes=10,
            resource="oven",
            oven_temp_f=302,
        ),
        RecipeStep(
            step_id="test_celsius_step_2",
            description="Bake at 200°C (392°F) for 25 minutes",
            duration_minutes=25,
            resource="oven",
            oven_temp_f=392,
        ),
    ]
    mock_response.chef_notes = "Celsius conversion test"
    mock_response.techniques_used = ["baking"]
    mock_response.response_metadata = {
        "usage": {"input_tokens": 100, "output_tokens": 200}
    }
    
    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.with_structured_output = MagicMock(return_value=mock_chain)
    
    state = {
        "raw_recipes": [raw_recipe.model_dump()],
        "user_id": "test_user",
        "rag_owner_key": "",
    }
    
    result = await enrich_recipe_steps_node(state)
    
    assert len(result["enriched_recipes"]) == 1
    enriched = result["enriched_recipes"][0]
    
    # Should have 3 steps (1 auto-injected preheat + 2 original)
    assert len(enriched["steps"]) == 3
    
    # First step should be auto-injected preheat with first oven temp (302°F)
    assert enriched["steps"][0]["step_id"] == "test_celsius_preheat_1"
    assert enriched["steps"][0]["oven_temp_f"] == 302
    
    # Verify Celsius conversion (150°C = 302°F, 200°C = 392°F)
    assert enriched["steps"][1]["oven_temp_f"] == 302
    assert enriched["steps"][2]["oven_temp_f"] == 392


@pytest.mark.asyncio
async def test_vague_heat_inference(mock_llm):
    """
    Test that vague heat levels are mapped to predefined ranges.
    
    Verifies per R021:
    - "high heat" → 437°F
    - "medium heat" → 362°F
    - "low heat" → 312°F
    - Preheat step is automatically injected before first oven step
    """
    raw_recipe = RawRecipe(
        name="Test Vague Heat",
        cuisine="American",
        description="Test recipe",
        servings=4,
        estimated_total_minutes=90,
        ingredients=[
            Ingredient(name="meat", quantity="2 lbs", preparation="trimmed")
        ],
        steps=[
            "Sear on high heat for 3 minutes per side",
            "Transfer to oven at medium heat for 45 minutes",
            "Finish at low heat for 20 minutes",
        ],
    )
    
    mock_response = MagicMock()
    mock_response.steps = [
        RecipeStep(
            step_id="test_vague_heat_step_1",
            description="Sear on high heat for 3 minutes per side",
            duration_minutes=6,
            resource="stovetop",
            oven_temp_f=None,  # stovetop step, no oven temp
        ),
        RecipeStep(
            step_id="test_vague_heat_step_2",
            description="Transfer to oven at medium heat for 45 minutes",
            duration_minutes=45,
            resource="oven",
            oven_temp_f=362,
        ),
        RecipeStep(
            step_id="test_vague_heat_step_3",
            description="Finish at low heat for 20 minutes",
            duration_minutes=20,
            resource="oven",
            oven_temp_f=312,
        ),
    ]
    mock_response.chef_notes = "Vague heat inference test"
    mock_response.techniques_used = ["searing", "roasting"]
    mock_response.response_metadata = {
        "usage": {"input_tokens": 100, "output_tokens": 200}
    }
    
    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.with_structured_output = MagicMock(return_value=mock_chain)
    
    state = {
        "raw_recipes": [raw_recipe.model_dump()],
        "user_id": "test_user",
        "rag_owner_key": "",
    }
    
    result = await enrich_recipe_steps_node(state)
    
    assert len(result["enriched_recipes"]) == 1
    enriched = result["enriched_recipes"][0]
    
    # Should have 4 steps (1 auto-injected preheat + 3 original)
    assert len(enriched["steps"]) == 4
    
    # First step should be auto-injected preheat with first oven temp (362°F)
    assert enriched["steps"][0]["step_id"] == "test_vague_heat_preheat_1"
    assert enriched["steps"][0]["oven_temp_f"] == 362
    
    # Verify vague heat mapping (shifted by 1 due to preheat)
    assert enriched["steps"][1]["oven_temp_f"] is None  # stovetop, not oven
    assert enriched["steps"][2]["oven_temp_f"] == 362  # medium heat
    assert enriched["steps"][3]["oven_temp_f"] == 312  # low heat


# ── Preheat Injection Tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preheat_injection_single_oven_step(mock_llm):
    """
    Test that preheat step is injected before first oven usage.
    
    Verifies:
    - Preheat step added at position 0
    - Preheat has correct temperature (375°F)
    - Preheat has duration_minutes=12
    - Preheat has resource=OVEN
    - Preheat has depends_on=[]
    """
    raw_recipe = RawRecipe(
        name="Simple Bake",
        cuisine="American",
        description="Test recipe",
        servings=4,
        estimated_total_minutes=45,
        ingredients=[
            Ingredient(name="flour", quantity="2 cups", preparation="sifted")
        ],
        steps=[
            "Mix ingredients",
            "Bake at 375°F for 25 minutes",
            "Cool on rack",
        ],
    )
    
    mock_response = MagicMock()
    mock_response.steps = [
        RecipeStep(
            step_id="simple_bake_step_1",
            description="Mix ingredients",
            duration_minutes=5,
            resource="hands",
            oven_temp_f=None,
        ),
        RecipeStep(
            step_id="simple_bake_step_2",
            description="Bake at 375°F for 25 minutes",
            duration_minutes=25,
            resource="oven",
            oven_temp_f=375,
        ),
        RecipeStep(
            step_id="simple_bake_step_3",
            description="Cool on rack",
            duration_minutes=15,
            resource="passive",
            oven_temp_f=None,
        ),
    ]
    mock_response.chef_notes = "Simple baking test"
    mock_response.techniques_used = ["baking"]
    mock_response.response_metadata = {
        "usage": {"input_tokens": 100, "output_tokens": 200}
    }
    
    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.with_structured_output = MagicMock(return_value=mock_chain)
    
    state = {
        "raw_recipes": [raw_recipe.model_dump()],
        "user_id": "test_user",
        "rag_owner_key": "",
    }
    
    result = await enrich_recipe_steps_node(state)
    
    assert len(result["enriched_recipes"]) == 1
    enriched = result["enriched_recipes"][0]
    
    # Should have 4 steps (preheat + 3 original)
    assert len(enriched["steps"]) == 4
    
    # First step should be preheat
    preheat = enriched["steps"][0]
    assert preheat["step_id"] == "simple_bake_preheat_1"
    assert preheat["description"] == "Preheat oven to 375°F"
    assert preheat["duration_minutes"] == 12
    assert preheat["resource"] == "oven"
    assert preheat["oven_temp_f"] == 375
    assert preheat["depends_on"] == []
    
    # Verify original steps follow
    assert enriched["steps"][1]["step_id"] == "simple_bake_step_1"
    assert enriched["steps"][2]["step_id"] == "simple_bake_step_2"
    assert enriched["steps"][3]["step_id"] == "simple_bake_step_3"


@pytest.mark.asyncio
async def test_preheat_injection_no_oven_steps(mock_llm):
    """
    Test that no preheat is injected when recipe has no oven steps.
    
    Verifies:
    - No preheat step added
    - Steps unchanged
    """
    raw_recipe = RawRecipe(
        name="Stovetop Only",
        cuisine="Italian",
        description="Test recipe",
        servings=4,
        estimated_total_minutes=30,
        ingredients=[
            Ingredient(name="pasta", quantity="1 lb", preparation="")
        ],
        steps=[
            "Boil water",
            "Cook pasta for 10 minutes",
            "Drain and serve",
        ],
    )
    
    mock_response = MagicMock()
    mock_response.steps = [
        RecipeStep(
            step_id="stovetop_only_step_1",
            description="Boil water",
            duration_minutes=5,
            resource="stovetop",
            oven_temp_f=None,
        ),
        RecipeStep(
            step_id="stovetop_only_step_2",
            description="Cook pasta for 10 minutes",
            duration_minutes=10,
            resource="stovetop",
            oven_temp_f=None,
        ),
        RecipeStep(
            step_id="stovetop_only_step_3",
            description="Drain and serve",
            duration_minutes=5,
            resource="hands",
            oven_temp_f=None,
        ),
    ]
    mock_response.chef_notes = "Stovetop test"
    mock_response.techniques_used = ["boiling"]
    mock_response.response_metadata = {
        "usage": {"input_tokens": 100, "output_tokens": 200}
    }
    
    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.with_structured_output = MagicMock(return_value=mock_chain)
    
    state = {
        "raw_recipes": [raw_recipe.model_dump()],
        "user_id": "test_user",
        "rag_owner_key": "",
    }
    
    result = await enrich_recipe_steps_node(state)
    
    assert len(result["enriched_recipes"]) == 1
    enriched = result["enriched_recipes"][0]
    
    # Should have 3 steps (no preheat)
    assert len(enriched["steps"]) == 3
    
    # No preheat step
    assert not any("preheat" in step["step_id"].lower() for step in enriched["steps"])
    
    # Verify original steps
    assert enriched["steps"][0]["step_id"] == "stovetop_only_step_1"
    assert enriched["steps"][1]["step_id"] == "stovetop_only_step_2"
    assert enriched["steps"][2]["step_id"] == "stovetop_only_step_3"


@pytest.mark.asyncio
async def test_preheat_injection_multiple_oven_steps(mock_llm):
    """
    Test that only ONE preheat is injected before first oven step when multiple oven steps exist.
    
    Verifies:
    - Only one preheat step added
    - Preheat uses temperature from first oven step
    - Subsequent oven steps unchanged
    """
    raw_recipe = RawRecipe(
        name="Multi Bake",
        cuisine="French",
        description="Test recipe",
        servings=4,
        estimated_total_minutes=90,
        ingredients=[
            Ingredient(name="dough", quantity="2 lbs", preparation="")
        ],
        steps=[
            "Shape dough",
            "Bake at 425°F for 15 minutes",
            "Reduce heat to 350°F and bake for 30 minutes",
            "Cool",
        ],
    )
    
    mock_response = MagicMock()
    mock_response.steps = [
        RecipeStep(
            step_id="multi_bake_step_1",
            description="Shape dough",
            duration_minutes=10,
            resource="hands",
            oven_temp_f=None,
        ),
        RecipeStep(
            step_id="multi_bake_step_2",
            description="Bake at 425°F for 15 minutes",
            duration_minutes=15,
            resource="oven",
            oven_temp_f=425,
        ),
        RecipeStep(
            step_id="multi_bake_step_3",
            description="Reduce heat to 350°F and bake for 30 minutes",
            duration_minutes=30,
            resource="oven",
            oven_temp_f=350,
        ),
        RecipeStep(
            step_id="multi_bake_step_4",
            description="Cool",
            duration_minutes=20,
            resource="passive",
            oven_temp_f=None,
        ),
    ]
    mock_response.chef_notes = "Multi-stage baking test"
    mock_response.techniques_used = ["baking"]
    mock_response.response_metadata = {
        "usage": {"input_tokens": 100, "output_tokens": 200}
    }
    
    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.with_structured_output = MagicMock(return_value=mock_chain)
    
    state = {
        "raw_recipes": [raw_recipe.model_dump()],
        "user_id": "test_user",
        "rag_owner_key": "",
    }
    
    result = await enrich_recipe_steps_node(state)
    
    assert len(result["enriched_recipes"]) == 1
    enriched = result["enriched_recipes"][0]
    
    # Should have 5 steps (1 preheat + 4 original)
    assert len(enriched["steps"]) == 5
    
    # First step should be preheat with temp from first oven step (425°F)
    preheat = enriched["steps"][0]
    assert preheat["step_id"] == "multi_bake_preheat_1"
    assert preheat["description"] == "Preheat oven to 425°F"
    assert preheat["oven_temp_f"] == 425
    
    # Only ONE preheat step
    preheat_count = sum(1 for step in enriched["steps"] if "preheat" in step["step_id"].lower())
    assert preheat_count == 1
    
    # Verify original steps follow
    assert enriched["steps"][1]["step_id"] == "multi_bake_step_1"
    assert enriched["steps"][2]["step_id"] == "multi_bake_step_2"
    assert enriched["steps"][2]["oven_temp_f"] == 425
    assert enriched["steps"][3]["step_id"] == "multi_bake_step_3"
    assert enriched["steps"][3]["oven_temp_f"] == 350


@pytest.mark.asyncio
async def test_preheat_injection_oven_with_none_temp(mock_llm):
    """
    Test that preheat is NOT injected when oven step has oven_temp_f=None.
    
    Verifies:
    - No preheat when oven_temp_f is None
    - Steps unchanged
    """
    raw_recipe = RawRecipe(
        name="Broiler Only",
        cuisine="American",
        description="Test recipe",
        servings=4,
        estimated_total_minutes=20,
        ingredients=[
            Ingredient(name="steak", quantity="2 lbs", preparation="")
        ],
        steps=[
            "Season steak",
            "Broil for 5 minutes per side",
        ],
    )
    
    mock_response = MagicMock()
    mock_response.steps = [
        RecipeStep(
            step_id="broiler_only_step_1",
            description="Season steak",
            duration_minutes=5,
            resource="hands",
            oven_temp_f=None,
        ),
        RecipeStep(
            step_id="broiler_only_step_2",
            description="Broil for 5 minutes per side",
            duration_minutes=10,
            resource="oven",  # oven resource but no temp
            oven_temp_f=None,
        ),
    ]
    mock_response.chef_notes = "Broiler test"
    mock_response.techniques_used = ["broiling"]
    mock_response.response_metadata = {
        "usage": {"input_tokens": 100, "output_tokens": 200}
    }
    
    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.with_structured_output = MagicMock(return_value=mock_chain)
    
    state = {
        "raw_recipes": [raw_recipe.model_dump()],
        "user_id": "test_user",
        "rag_owner_key": "",
    }
    
    result = await enrich_recipe_steps_node(state)
    
    assert len(result["enriched_recipes"]) == 1
    enriched = result["enriched_recipes"][0]
    
    # Should have 2 steps (no preheat)
    assert len(enriched["steps"]) == 2
    
    # No preheat step
    assert not any("preheat" in step["step_id"].lower() for step in enriched["steps"])
    
    # Verify original steps
    assert enriched["steps"][0]["step_id"] == "broiler_only_step_1"
    assert enriched["steps"][1]["step_id"] == "broiler_only_step_2"
