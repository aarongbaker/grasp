"""
tests/test_generator_integration.py
Integration test for the real recipe generator node.

Calls Claude for real — requires ANTHROPIC_API_KEY in environment.
Run separately from the Phase 3 suite:
    pytest tests/test_generator_integration.py -v

Skipped automatically if ANTHROPIC_API_KEY is not set.
"""

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.graph.nodes.generator import (
    RecipeGenerationOutput,
    _build_mixed_origin_system_prompt,
    _build_retry_human_prompt,
    _build_retry_system_prompt,
    _build_system_prompt,
    _derive_recipe_count,
    recipe_generator_node,
)
from app.models.enums import ErrorType, MealType, Occasion
from app.models.pipeline import (
    DinnerConcept,
    GenerationRetryReason,
    PlannerLibraryAuthoredRecipeAnchor,
    PlannerLibraryCookbookPlanningMode,
    PlannerLibraryCookbookTarget,
)
from app.models.recipe import Ingredient, RawRecipe
from app.models.scheduling import OneOvenConflictSummary

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
        {
            "name": "Sous vide circulator",
            "category": "precision",
            "unlocks_techniques": ["precise-temperature cooking"],
        },
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


def test_build_system_prompt_prefers_single_oven_compatible_menus(dinner_concept):
    """Single-oven prompts should name the oven-compatibility contract explicitly."""
    prompt = _build_system_prompt(
        concept=dinner_concept,
        kitchen_config={"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        equipment=[],
        recipe_count=3,
    )

    assert "single-oven kitchens" in prompt
    assert "can actually be executed on one oven without temperature-conflict overlap" in prompt
    assert "within about 15°F" in prompt
    assert "long low braises with high-heat bakes or desserts" in prompt
    assert "one oven-heavy dish plus stovetop/passive complements" in prompt
    assert "choose different dishes or cooking methods instead of returning an impossible plan" in prompt


def test_build_mixed_origin_system_prompt_prefers_anchor_compatible_oven_load(dinner_concept):
    """Mixed-origin prompts should preserve the same single-oven compatibility guidance."""
    anchor_recipe = RawRecipe(
        name="Anchored Short Rib Braise",
        description="An authored braise anchor.",
        servings=4,
        cuisine="French",
        estimated_total_minutes=210,
        ingredients=[Ingredient(name="short ribs", quantity="2 kg")],
        steps=[
            "Brown the ribs in a Dutch oven.",
            "Add stock and aromatics.",
            "Cover and braise in a 150°C oven for 3 hours.",
        ],
    )

    prompt = _build_mixed_origin_system_prompt(
        concept=dinner_concept,
        kitchen_config={"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        equipment=[],
        anchor_recipe=anchor_recipe,
        complement_count=2,
    )

    assert "fixed anchor recipe" in prompt.lower()
    assert "single-oven kitchens" in prompt
    assert "can actually be executed on one oven without temperature-conflict overlap" in prompt
    assert "within about 15°F" in prompt
    assert "long low braises with high-heat bakes or desserts" in prompt
    assert "one oven-heavy dish plus stovetop/passive complements" in prompt
    assert "choose different dishes or cooking methods instead of returning an impossible plan" in prompt


def test_build_system_prompt_relaxes_parallel_temp_guidance_with_second_oven(dinner_concept):
    """Second-oven kitchens should still prefer compatibility without forbidding mixed oven temps."""
    prompt = _build_system_prompt(
        concept=dinner_concept,
        kitchen_config={"max_burners": 4, "max_oven_racks": 2, "has_second_oven": True},
        equipment=[],
        recipe_count=3,
    )

    assert "second oven means parallel dishes may use meaningfully different temperatures" in prompt
    assert "single-oven kitchens" not in prompt


def test_build_retry_system_prompt_includes_typed_single_oven_conflict_context(dinner_concept):
    retry_reason = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary(
            classification="irreconcilable",
            tolerance_f=15,
            has_second_oven=False,
            temperature_gap_f=45,
            blocking_recipe_names=["Braised Short Ribs", "Chocolate Souffle"],
            affected_step_ids=["short_ribs_braise", "souffle_bake"],
            remediation={
                "requires_resequencing": False,
                "suggested_actions": ["Use a second oven or change recipes."],
                "notes": "Braised Short Ribs at 300°F conflicts with Chocolate Souffle at 425°F.",
            },
        ),
        detail="Braised Short Ribs at 300°F conflicts with Chocolate Souffle at 425°F from 18:00 to 18:20.",
        attempt=1,
    )

    prompt = _build_retry_system_prompt(
        concept=dinner_concept,
        kitchen_config={"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        equipment=[],
        recipe_count=3,
        retry_reason=retry_reason,
    )

    assert "performing corrective regeneration after the scheduler rejected the previous menu" in prompt
    assert "SCHEDULER CONFLICT CONTEXT (AUTHORITATIVE)" in prompt
    assert "Triggering node: dag_merger" in prompt
    assert "Allowed oven temperature tolerance: 15°F" in prompt
    assert "Reported conflicting temperature gap: 45°F" in prompt
    assert "Blocking recipe names: Braised Short Ribs, Chocolate Souffle" in prompt
    assert "Affected scheduler step ids: short_ribs_braise, souffle_bake" in prompt
    assert "Do NOT reproduce the same beyond-tolerance overlap shape" in prompt
    assert "If more than one oven-using dish would overlap in a single-oven kitchen" in prompt
    assert "you MUST change the menu before proceeding" in prompt


def test_build_retry_system_prompt_relaxes_parallel_temperature_rule_with_second_oven(dinner_concept):
    retry_reason = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary(
            classification="irreconcilable",
            tolerance_f=15,
            has_second_oven=True,
            temperature_gap_f=60,
            blocking_recipe_names=["Duck Confit", "Tarte Tatin"],
        ),
        detail="Retry context imported from a mixed-origin failure.",
        attempt=2,
    )

    prompt = _build_retry_system_prompt(
        concept=dinner_concept,
        kitchen_config={"max_burners": 4, "max_oven_racks": 2, "has_second_oven": True},
        equipment=[],
        recipe_count=3,
        retry_reason=retry_reason,
    )

    assert "parallel oven dishes may use meaningfully different temperatures because a second oven is available" in prompt
    assert "If more than one oven-using dish would overlap in a single-oven kitchen" not in prompt


def test_build_retry_human_prompt_names_conflict_shape(dinner_concept):
    retry_reason = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary(
            classification="irreconcilable",
            tolerance_f=15,
            has_second_oven=False,
            blocking_recipe_names=["Ratatouille Tian", "Apple Galette"],
        ),
        detail="Ratatouille Tian at 375°F conflicts with Apple Galette at 410°F.",
        attempt=1,
    )

    prompt = _build_retry_human_prompt(3, dinner_concept, retry_reason)

    assert "Regenerate exactly 3 recipes" in prompt
    assert "Ratatouille Tian, Apple Galette" in prompt
    assert "single-oven kitchen" in prompt
    assert "does not repeat that conflict pattern" in prompt


def test_build_retry_prompt_degrades_gracefully_when_optional_conflict_metadata_is_missing(dinner_concept):
    retry_reason = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary(
            classification="irreconcilable",
            tolerance_f=15,
            has_second_oven=False,
        ),
        detail="Scheduler reported a single-oven temperature conflict.",
        attempt=1,
    )

    system_prompt = _build_retry_system_prompt(
        concept=dinner_concept,
        kitchen_config={"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        equipment=[],
        recipe_count=3,
        retry_reason=retry_reason,
    )
    human_prompt = _build_retry_human_prompt(3, dinner_concept, retry_reason)

    assert "Allowed oven temperature tolerance: 15°F" in system_prompt
    assert "Blocking recipe names:" not in system_prompt
    assert "Affected scheduler step ids:" not in system_prompt
    assert "Reported conflicting temperature gap:" not in system_prompt
    assert "the conflicting dishes" in human_prompt
    assert "does not repeat that conflict pattern" in human_prompt


@pytest.fixture
def compatible_candidate_menu() -> RecipeGenerationOutput:
    return RecipeGenerationOutput(
        recipes=[
            RawRecipe(
                name="Roast Chicken",
                description="Fixture compatible main.",
                servings=4,
                cuisine="French",
                estimated_total_minutes=75,
                ingredients=[Ingredient(name="chicken", quantity="1 whole")],
                steps=[
                    "Season the chicken.",
                    "Roast in a 375°F oven for 50 minutes.",
                    "Rest and carve.",
                ],
            ),
            RawRecipe(
                name="Apple Tart",
                description="Fixture compatible dessert.",
                servings=4,
                cuisine="French",
                estimated_total_minutes=55,
                ingredients=[Ingredient(name="apples", quantity="4")],
                steps=[
                    "Roll the pastry.",
                    "Bake in a 380°F oven until browned.",
                    "Cool slightly before serving.",
                ],
            ),
            RawRecipe(
                name="Frisee Salad",
                description="Fixture salad.",
                servings=4,
                cuisine="French",
                estimated_total_minutes=15,
                ingredients=[Ingredient(name="frisee", quantity="1 head")],
                steps=[
                    "Wash the frisee.",
                    "Dress lightly.",
                    "Serve immediately.",
                ],
            ),
        ]
    )


@pytest.fixture
def incompatible_candidate_menu() -> RecipeGenerationOutput:
    return RecipeGenerationOutput(
        recipes=[
            RawRecipe(
                name="Short Rib Braise",
                description="Fixture incompatible main.",
                servings=4,
                cuisine="French",
                estimated_total_minutes=220,
                ingredients=[Ingredient(name="short ribs", quantity="2 kg")],
                steps=[
                    "Brown the short ribs.",
                    "Braise in a 300°F oven for 3 hours.",
                    "Rest before serving.",
                ],
            ),
            RawRecipe(
                name="Molten Cake",
                description="Fixture incompatible dessert.",
                servings=4,
                cuisine="French",
                estimated_total_minutes=35,
                ingredients=[Ingredient(name="dark chocolate", quantity="200 g")],
                steps=[
                    "Prepare the batter.",
                    "Bake in a 425°F oven for 12 minutes.",
                    "Serve immediately.",
                ],
            ),
            RawRecipe(
                name="Green Bean Salad",
                description="Fixture salad.",
                servings=4,
                cuisine="French",
                estimated_total_minutes=20,
                ingredients=[Ingredient(name="green beans", quantity="500 g")],
                steps=[
                    "Blanch the beans.",
                    "Dress with shallot vinaigrette.",
                    "Serve warm.",
                ],
            ),
        ]
    )


@pytest.mark.asyncio
async def test_generator_node_prefers_compatible_candidate_in_single_oven_free_text_flow(
    dinner_concept,
    compatible_candidate_menu,
    incompatible_candidate_menu,
):
    state = {
        "concept": dinner_concept.model_dump(mode="json"),
        "kitchen_config": {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        "equipment": [],
        "errors": [],
    }

    invoke_mock = AsyncMock(
        side_effect=[
            (incompatible_candidate_menu, {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}),
            (compatible_candidate_menu, {"prompt_tokens": 11, "completion_tokens": 21, "total_tokens": 32}),
            (incompatible_candidate_menu, {"prompt_tokens": 12, "completion_tokens": 22, "total_tokens": 34}),
        ]
    )

    with patch("app.graph.nodes.generator._invoke_recipe_generation", invoke_mock):
        result = await recipe_generator_node(state)

    assert [recipe["name"] for recipe in result["raw_recipes"]] == [
        "Roast Chicken",
        "Apple Tart",
        "Frisee Salad",
    ]
    assert result["token_usage"] == [
        {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        {"prompt_tokens": 11, "completion_tokens": 21, "total_tokens": 32},
        {"prompt_tokens": 12, "completion_tokens": 22, "total_tokens": 34},
    ]
    assert invoke_mock.await_count == 3


@pytest.mark.asyncio
async def test_generator_node_relaxes_candidate_bias_with_second_oven(
    dinner_concept,
    compatible_candidate_menu,
    incompatible_candidate_menu,
):
    state = {
        "concept": dinner_concept.model_dump(mode="json"),
        "kitchen_config": {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": True},
        "equipment": [],
        "errors": [],
    }

    invoke_mock = AsyncMock(
        side_effect=[
            (incompatible_candidate_menu, {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}),
            (compatible_candidate_menu, {"prompt_tokens": 11, "completion_tokens": 21, "total_tokens": 32}),
            (compatible_candidate_menu, {"prompt_tokens": 12, "completion_tokens": 22, "total_tokens": 34}),
        ]
    )

    with patch("app.graph.nodes.generator._invoke_recipe_generation", invoke_mock):
        result = await recipe_generator_node(state)

    assert [recipe["name"] for recipe in result["raw_recipes"]] == [
        "Short Rib Braise",
        "Molten Cake",
        "Green Bean Salad",
    ]
    assert invoke_mock.await_count == 3


@pytest.mark.asyncio
async def test_generator_node_prefers_compatible_complements_for_planner_authored_anchor(
    dinner_concept,
    compatible_candidate_menu,
    incompatible_candidate_menu,
):
    anchor_recipe = RawRecipe(
        name="Anchored Duck Confit",
        description="Authored fixture anchor.",
        servings=4,
        cuisine="French",
        estimated_total_minutes=180,
        ingredients=[Ingredient(name="duck legs", quantity="4")],
        steps=[
            "Cure the duck overnight.",
            "Slow-roast in a 325°F oven until tender.",
            "Crisp before serving.",
        ],
    )
    concept = DinnerConcept(
        free_text=dinner_concept.free_text,
        guest_count=dinner_concept.guest_count,
        meal_type=dinner_concept.meal_type,
        occasion=dinner_concept.occasion,
        dietary_restrictions=dinner_concept.dietary_restrictions,
        concept_source="planner_authored_anchor",
        planner_authored_recipe_anchor=PlannerLibraryAuthoredRecipeAnchor(
            recipe_id=uuid.uuid4(),
            title=anchor_recipe.name,
        ),
    )
    state = {
        "concept": concept.model_dump(mode="json"),
        "kitchen_config": {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        "equipment": [],
        "errors": [],
    }

    invoke_mock = AsyncMock(
        side_effect=[
            (
                RecipeGenerationOutput(recipes=incompatible_candidate_menu.recipes[:2]),
                {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            ),
            (
                RecipeGenerationOutput(recipes=compatible_candidate_menu.recipes[:2]),
                {"prompt_tokens": 11, "completion_tokens": 21, "total_tokens": 32},
            ),
            (
                RecipeGenerationOutput(recipes=incompatible_candidate_menu.recipes[:2]),
                {"prompt_tokens": 12, "completion_tokens": 22, "total_tokens": 34},
            ),
        ]
    )

    with (
        patch("app.graph.nodes.generator.build_planner_authored_anchor_raw_recipes", AsyncMock(return_value=[anchor_recipe])),
        patch("app.graph.nodes.generator._invoke_recipe_generation", invoke_mock),
    ):
        result = await recipe_generator_node(state)

    assert [recipe["name"] for recipe in result["raw_recipes"]] == [
        "Anchored Duck Confit",
        "Roast Chicken",
        "Apple Tart",
    ]
    assert invoke_mock.await_count == 3


@pytest.mark.asyncio
async def test_generator_node_keeps_planner_cookbook_strict_mode_seed_only(dinner_concept):
    seeded_recipe = RawRecipe(
        name="Cookbook Cassoulet",
        description="Cookbook seed.",
        servings=4,
        cuisine="French",
        estimated_total_minutes=150,
        ingredients=[Ingredient(name="beans", quantity="500 g")],
        steps=["Soak the beans.", "Bake in a 350°F oven until tender.", "Rest before serving."],
    )
    concept = DinnerConcept(
        free_text=dinner_concept.free_text,
        guest_count=dinner_concept.guest_count,
        meal_type=dinner_concept.meal_type,
        occasion=dinner_concept.occasion,
        dietary_restrictions=dinner_concept.dietary_restrictions,
        concept_source="planner_cookbook_target",
        planner_cookbook_target=PlannerLibraryCookbookTarget(
            cookbook_id=uuid.uuid4(),
            name="French Classics",
            mode=PlannerLibraryCookbookPlanningMode.STRICT,
        ),
    )
    state = {
        "concept": concept.model_dump(mode="json"),
        "kitchen_config": {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        "equipment": [],
        "errors": [],
    }

    with (
        patch("app.graph.nodes.generator.build_planner_cookbook_target_raw_recipes", AsyncMock(return_value=[seeded_recipe])),
        patch("app.graph.nodes.generator._invoke_recipe_generation", AsyncMock()) as invoke_mock,
    ):
        result = await recipe_generator_node(state)

    assert [recipe["name"] for recipe in result["raw_recipes"]] == ["Cookbook Cassoulet"]
    invoke_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_generator_node_prefers_compatible_complements_for_planner_cookbook_biased_mode(
    dinner_concept,
    compatible_candidate_menu,
    incompatible_candidate_menu,
):
    anchor_recipe = RawRecipe(
        name="Cookbook Duck Legs",
        description="Cookbook-authored seed anchor.",
        servings=4,
        cuisine="French",
        estimated_total_minutes=180,
        ingredients=[Ingredient(name="duck legs", quantity="4")],
        steps=[
            "Cure the duck overnight.",
            "Slow-roast in a 325°F oven until tender.",
            "Crisp before serving.",
        ],
    )
    concept = DinnerConcept(
        free_text=dinner_concept.free_text,
        guest_count=dinner_concept.guest_count,
        meal_type=dinner_concept.meal_type,
        occasion=dinner_concept.occasion,
        dietary_restrictions=dinner_concept.dietary_restrictions,
        concept_source="planner_cookbook_target",
        planner_cookbook_target=PlannerLibraryCookbookTarget(
            cookbook_id=uuid.uuid4(),
            name="French Classics",
            mode=PlannerLibraryCookbookPlanningMode.COOKBOOK_BIASED,
        ),
    )
    state = {
        "concept": concept.model_dump(mode="json"),
        "kitchen_config": {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        "equipment": [],
        "errors": [],
    }

    invoke_mock = AsyncMock(
        side_effect=[
            (
                RecipeGenerationOutput(recipes=incompatible_candidate_menu.recipes[:2]),
                {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            ),
            (
                RecipeGenerationOutput(recipes=compatible_candidate_menu.recipes[:2]),
                {"prompt_tokens": 11, "completion_tokens": 21, "total_tokens": 32},
            ),
            (
                RecipeGenerationOutput(recipes=incompatible_candidate_menu.recipes[:2]),
                {"prompt_tokens": 12, "completion_tokens": 22, "total_tokens": 34},
            ),
        ]
    )

    with (
        patch("app.graph.nodes.generator.build_planner_cookbook_target_raw_recipes", AsyncMock(return_value=[anchor_recipe])),
        patch("app.graph.nodes.generator._invoke_recipe_generation", invoke_mock),
    ):
        result = await recipe_generator_node(state)

    assert [recipe["name"] for recipe in result["raw_recipes"]] == [
        "Cookbook Duck Legs",
        "Roast Chicken",
        "Apple Tart",
    ]
    assert invoke_mock.await_count == 3


@pytest.mark.asyncio
async def test_generator_node_uses_retry_prompt_builder_for_single_oven_repair(
    dinner_concept,
    compatible_candidate_menu,
):
    retry_reason = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary(
            classification="irreconcilable",
            tolerance_f=15,
            has_second_oven=False,
            temperature_gap_f=50,
            blocking_recipe_names=["Braised Short Ribs", "Chocolate Souffle"],
            affected_step_ids=["short_ribs_braise", "souffle_bake"],
        ),
        detail="Braised Short Ribs at 300°F conflicts with Chocolate Souffle at 425°F.",
        attempt=1,
    )
    state = {
        "concept": dinner_concept.model_dump(mode="json"),
        "kitchen_config": {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        "equipment": [],
        "errors": [],
        "generation_attempt": 2,
        "generation_retry_reason": retry_reason.model_dump(mode="json"),
        "generation_history": [
            {
                "attempt": 2,
                "trigger": "auto_repair",
                "recipe_names": ["Braised Short Ribs", "Chocolate Souffle"],
                "retry_reason": retry_reason.model_dump(mode="json"),
            }
        ],
        "enriched_recipes": [{"name": "stale enriched"}],
        "validated_recipes": [{"name": "stale validated"}],
        "recipe_dags": [{"recipe_name": "stale dag"}],
        "merged_dag": {"recipe_count": 1},
        "schedule": {"summary": "stale schedule"},
    }

    with patch(
        "app.graph.nodes.generator._generate_ranked_recipe_candidates",
        AsyncMock(return_value=(compatible_candidate_menu, [{"total_tokens": 30}], {})),
    ) as ranked_mock:
        result = await recipe_generator_node(state)

    assert [recipe["name"] for recipe in result["raw_recipes"]] == [
        "Roast Chicken",
        "Apple Tart",
        "Frisee Salad",
    ]
    kwargs = ranked_mock.await_args.kwargs
    assert "SCHEDULER CONFLICT CONTEXT (AUTHORITATIVE)" in kwargs["system_prompt"]
    assert "Braised Short Ribs, Chocolate Souffle" in kwargs["system_prompt"]
    assert "Do NOT reproduce the same beyond-tolerance overlap shape" in kwargs["system_prompt"]
    assert "Regenerate exactly 3 recipes" in kwargs["human_prompt"]
    assert "does not repeat that conflict pattern" in kwargs["human_prompt"]
    assert result["token_usage"] == [{"total_tokens": 30}]
    assert result["generation_retry_reason"] is None
    assert result["generation_history"] == [
        {
            "attempt": 2,
            "trigger": "auto_repair",
            "recipe_names": ["Roast Chicken", "Apple Tart", "Frisee Salad"],
            "retry_reason": retry_reason.model_dump(mode="json"),
        }
    ]
    assert result["enriched_recipes"] == []
    assert result["validated_recipes"] == []
    assert result["recipe_dags"] == []
    assert result["merged_dag"] is None
    assert result["schedule"] is None


@pytest.mark.asyncio
async def test_generator_node_records_initial_attempt_history_without_retry_context(
    dinner_concept,
    compatible_candidate_menu,
):
    state = {
        "concept": dinner_concept.model_dump(mode="json"),
        "kitchen_config": {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        "equipment": [],
        "errors": [],
        "generation_attempt": 1,
        "generation_history": [],
    }

    with patch(
        "app.graph.nodes.generator._generate_ranked_recipe_candidates",
        AsyncMock(return_value=(compatible_candidate_menu, [{"prompt_tokens": 10, "completion_tokens": 20}], {})),
    ):
        result = await recipe_generator_node(state)

    assert result["generation_history"] == [
        {
            "attempt": 1,
            "trigger": "initial",
            "recipe_names": ["Roast Chicken", "Apple Tart", "Frisee Salad"],
            "retry_reason": None,
        }
    ]
    assert result["token_usage"] == [{"prompt_tokens": 10, "completion_tokens": 20}]
    assert result["generation_retry_reason"] is None


@pytest.mark.asyncio
async def test_generator_node_records_retry_attempt_history_without_overwriting_prior_attempts(
    dinner_concept,
    compatible_candidate_menu,
):
    retry_reason = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary(
            classification="irreconcilable",
            tolerance_f=15,
            has_second_oven=False,
            temperature_gap_f=50,
            blocking_recipe_names=["Braised Short Ribs", "Chocolate Souffle"],
        ),
        detail="Braised Short Ribs at 300°F conflicts with Chocolate Souffle at 425°F.",
        attempt=1,
    )
    state = {
        "concept": dinner_concept.model_dump(mode="json"),
        "kitchen_config": {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False},
        "equipment": [],
        "errors": [],
        "generation_attempt": 2,
        "generation_retry_reason": retry_reason.model_dump(mode="json"),
        "generation_history": [
            {
                "attempt": 1,
                "trigger": "initial",
                "recipe_names": ["Braised Short Ribs", "Chocolate Souffle", "Frisee Salad"],
                "retry_reason": None,
            }
        ],
    }

    with patch(
        "app.graph.nodes.generator._generate_ranked_recipe_candidates",
        AsyncMock(
            return_value=(
                compatible_candidate_menu,
                [
                    {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                    {"prompt_tokens": 11, "completion_tokens": 21, "total_tokens": 32},
                    {"prompt_tokens": 12, "completion_tokens": 22, "total_tokens": 34},
                ],
                {},
            )
        ),
    ):
        result = await recipe_generator_node(state)

    assert result["generation_history"] == [
        {
            "attempt": 1,
            "trigger": "initial",
            "recipe_names": ["Braised Short Ribs", "Chocolate Souffle", "Frisee Salad"],
            "retry_reason": None,
        },
        {
            "attempt": 2,
            "trigger": "auto_repair",
            "recipe_names": ["Roast Chicken", "Apple Tart", "Frisee Salad"],
            "retry_reason": retry_reason.model_dump(mode="json"),
        },
    ]
    assert result["token_usage"] == [
        {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        {"prompt_tokens": 11, "completion_tokens": 21, "total_tokens": 32},
        {"prompt_tokens": 12, "completion_tokens": 22, "total_tokens": 34},
    ]
    assert result["generation_retry_reason"] is None


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
