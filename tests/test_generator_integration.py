"""
tests/test_generator_integration.py
Integration test for the real recipe generator node.

Calls Claude for real — requires ANTHROPIC_API_KEY in environment.
Run separately from the Phase 3 suite:
    pytest tests/test_generator_integration.py -v

Skipped automatically if ANTHROPIC_API_KEY is not set.
"""

import os

import pytest
import pytest_asyncio

from graph.nodes.generator import (
    RecipeGenerationOutput,
    _build_system_prompt,
    _derive_recipe_count,
    recipe_generator_node,
)
from models.enums import MealType, Occasion
from models.pipeline import DinnerConcept
from models.recipe import RawRecipe

SKIP_REASON = "ANTHROPIC_API_KEY not set — skipping integration test"


@pytest.fixture
def dinner_concept() -> DinnerConcept:
    return DinnerConcept(
        free_text="A French dinner party with braised meat, a rich side, and a chocolate dessert.",
        guest_count=4,
        meal_type=MealType.DINNER,
        occasion=Occasion.DINNER_PARTY,
        dietary_restrictions=[],
    )


@pytest.fixture
def casual_lunch_concept() -> DinnerConcept:
    return DinnerConcept(
        free_text="A quick casual lunch, something light and fresh.",
        guest_count=2,
        meal_type=MealType.LUNCH,
        occasion=Occasion.CASUAL,
        dietary_restrictions=["gluten-free"],
    )


# ── Unit tests for helpers (no API call) ─────────────────────────────────────

def test_derive_recipe_count():
    """Verify the lookup table returns expected counts."""
    assert _derive_recipe_count(MealType.DINNER, Occasion.DINNER_PARTY) == 3
    assert _derive_recipe_count(MealType.LUNCH, Occasion.CASUAL) == 1
    assert _derive_recipe_count(MealType.DINNER, Occasion.TASTING_MENU) == 5


def test_build_system_prompt_includes_concept(dinner_concept):
    """System prompt should include all concept fields."""
    prompt = _build_system_prompt(
        concept=dinner_concept,
        kitchen_config={"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        equipment=[],
        recipe_count=3,
    )
    assert "French dinner party" in prompt
    assert "Guest count: 4" in prompt
    assert "Number of courses to generate: 3" in prompt
    assert "dinner_party" in prompt


def test_build_system_prompt_includes_equipment(dinner_concept):
    """System prompt should format equipment with unlocked techniques."""
    equipment = [
        {"name": "Sous vide circulator", "category": "precision", "unlocks_techniques": ["precise-temperature cooking"]},
        {"name": "Stand mixer", "category": "baking", "unlocks_techniques": ["laminated doughs", "meringue"]},
    ]
    prompt = _build_system_prompt(
        concept=dinner_concept,
        kitchen_config={"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        equipment=equipment,
        recipe_count=3,
    )
    assert "Sous vide circulator" in prompt
    assert "precise-temperature cooking" in prompt
    assert "Stand mixer" in prompt


def test_build_system_prompt_dietary_restrictions(casual_lunch_concept):
    """Dietary restrictions should appear in the prompt."""
    prompt = _build_system_prompt(
        concept=casual_lunch_concept,
        kitchen_config={"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        equipment=[],
        recipe_count=1,
    )
    assert "gluten-free" in prompt


# ── Integration tests (real Claude API) ──────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_generator_node_produces_valid_recipes(dinner_concept):
    """Call the real generator node and validate output structure."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(SKIP_REASON)

    state = {
        "concept": dinner_concept.model_dump(),
        "kitchen_config": {
            "max_burners": 4,
            "max_oven_racks": 2,
            "has_second_oven": False,
        },
        "equipment": [],
        "errors": [],
    }

    result = await recipe_generator_node(state)

    # Should not have errors
    assert "errors" not in result or len(result.get("errors", [])) == 0, (
        f"Generator returned errors: {result.get('errors')}"
    )

    raw_recipes = result["raw_recipes"]
    expected_count = _derive_recipe_count(MealType.DINNER, Occasion.DINNER_PARTY)

    # Validate each recipe against the RawRecipe schema
    for recipe_dict in raw_recipes:
        recipe = RawRecipe.model_validate(recipe_dict)
        assert recipe.name, "Recipe must have a name"
        assert recipe.description, "Recipe must have a description"
        assert recipe.servings > 0, "Servings must be positive"
        assert recipe.cuisine, "Recipe must have cuisine attribution"
        assert recipe.estimated_total_minutes > 0, "Must have estimated time"
        assert len(recipe.ingredients) > 0, "Must have at least one ingredient"
        assert len(recipe.steps) >= 3, "Must have at least 3 steps"

        # Each ingredient should have name and quantity
        for ing in recipe.ingredients:
            assert ing.name, "Ingredient must have a name"
            assert ing.quantity, "Ingredient must have a quantity"

    # Recipe count should match or be close to expected
    assert len(raw_recipes) >= 1, "Must generate at least one recipe"
    print(f"\nGenerated {len(raw_recipes)} recipes (expected {expected_count}):")
    for r in raw_recipes:
        print(f"  - {r['name']} ({r['cuisine']}, ~{r['estimated_total_minutes']} min)")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generator_node_respects_dietary_restrictions(casual_lunch_concept):
    """Verify dietary restrictions are respected in output."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(SKIP_REASON)

    state = {
        "concept": casual_lunch_concept.model_dump(),
        "kitchen_config": {
            "max_burners": 4,
            "max_oven_racks": 2,
            "has_second_oven": False,
        },
        "equipment": [],
        "errors": [],
    }

    result = await recipe_generator_node(state)
    assert "errors" not in result or len(result.get("errors", [])) == 0

    raw_recipes = result["raw_recipes"]
    assert len(raw_recipes) >= 1

    # Basic check: no ingredient should explicitly contain "flour" or "bread"
    # (common gluten sources) — this is a heuristic, not exhaustive
    gluten_terms = {"wheat flour", "all-purpose flour", "bread flour", "pasta", "breadcrumbs"}
    for recipe_dict in raw_recipes:
        recipe = RawRecipe.model_validate(recipe_dict)
        for ing in recipe.ingredients:
            ing_lower = ing.name.lower()
            for term in gluten_terms:
                assert term not in ing_lower, (
                    f"Gluten-free restriction violated: ingredient '{ing.name}' "
                    f"in recipe '{recipe.name}' contains '{term}'"
                )

    print(f"\nGluten-free check passed for {len(raw_recipes)} recipe(s)")
