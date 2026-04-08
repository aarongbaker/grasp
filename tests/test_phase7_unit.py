"""
tests/test_phase7_unit.py
Unit tests for Phase 7: Schedule Renderer.

These tests call the renderer's internal functions directly — no graph,
no database, no LLM. They verify:
  - Timeline construction: ScheduledStep → TimelineEntry (deterministic)
  - Fallback summary generation (no LLM)
  - Full node function with mocked LLM
  - Error handling: missing merged_dag, LLM failure (recoverable)

This module also carries generator-node unit coverage for deterministic
cookbook/authored seeding branches because they share the same Phase 7
pipeline verification lane in this repo.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.generator import (
    RecipeGenerationOutput,
    _derive_recipe_count,
    build_authored_raw_recipes,
    build_cookbook_raw_recipes,
    build_planner_authored_anchor_raw_recipes,
    recipe_generator_node,
)
from app.graph.nodes.renderer import (
    ScheduleSummaryOutput,
    _build_summary_prompt,
    _build_timeline,
    _build_timeline_entry,
    _fallback_error_summary,
    _fallback_summary,
    schedule_renderer_node,
)
from app.models.authored_recipe import AuthoredRecipeCreate, AuthoredRecipeRecord
from app.models.recipe import RecipeProvenance
from app.models.enums import ErrorType, MealType, Occasion, Resource
from app.models.pipeline import (
    DinnerConcept,
    PlannerLibraryAuthoredRecipeAnchor,
    PlannerLibraryCookbookPlanningMode,
    PlannerLibraryCookbookTarget,
    SelectedAuthoredRecipe,
    SelectedCookbookRecipe,
)
from app.models.scheduling import (
    MergedDAG,
    NaturalLanguageSchedule,
    OneOvenConflictSummary,
    ScheduledStep,
    TimelineEntry,
)
from tests.fixtures.recipes import AUTHORED_BRAISED_CHICKEN
from tests.fixtures.schedules import (
    MERGED_DAG_FULL,
    MERGED_DAG_TWO_RECIPE,
    NATURAL_LANGUAGE_SCHEDULE_FULL,
    NATURAL_LANGUAGE_SCHEDULE_TWO_RECIPE,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

CONCEPT_DICT = DinnerConcept(
    free_text="A special dinner party with short ribs, potato puree, and chocolate fondant.",
    guest_count=4,
    meal_type=MealType.DINNER,
    occasion=Occasion.DINNER_PARTY,
    dietary_restrictions=[],
).model_dump()


COOKBOOK_CONCEPT_DICT = DinnerConcept(
    free_text="Cookbook-selected recipes: Roast chicken with bread salad.",
    guest_count=4,
    meal_type=MealType.DINNER,
    occasion=Occasion.DINNER_PARTY,
    dietary_restrictions=[],
    concept_source="cookbook",
    selected_recipes=[
        SelectedCookbookRecipe(
            chunk_id=uuid.uuid4(),
            book_id=uuid.uuid4(),
            book_title="The French Laundry Cookbook",
            text="""Roast Chicken with Bread Salad
Ingredients:
- 1 whole chicken
- 2 tbsp olive oil
- 1 loaf country bread
Method:
1. Season the chicken generously and let it rest at room temperature.
2. Roast the chicken at 220°C until the juices run clear.
3. Toss torn bread with pan juices and serve alongside the carved chicken.
""",
            chapter="Poultry",
            page_number=87,
        )
    ],
).model_dump()

_AUTHORED_RECIPE_ID = uuid.uuid4()
_AUTHORED_CONCEPT = DinnerConcept(
    free_text="Schedule my saved braised chicken for Saturday service.",
    guest_count=4,
    meal_type=MealType.DINNER,
    occasion=Occasion.CASUAL,
    dietary_restrictions=[],
    concept_source="authored",
    selected_authored_recipe=SelectedAuthoredRecipe(
        recipe_id=_AUTHORED_RECIPE_ID,
        title=AUTHORED_BRAISED_CHICKEN.title,
    ),
)
AUTHORED_CONCEPT_DICT = _AUTHORED_CONCEPT.model_dump(mode="json")

PLANNER_AUTHORED_ANCHOR_CONCEPT = DinnerConcept(
    free_text="Plan a balanced dinner around my saved braised chicken.",
    guest_count=4,
    meal_type=MealType.DINNER,
    occasion=Occasion.DINNER_PARTY,
    dietary_restrictions=["No shellfish"],
    concept_source="planner_authored_anchor",
    planner_authored_recipe_anchor=PlannerLibraryAuthoredRecipeAnchor(
        recipe_id=_AUTHORED_RECIPE_ID,
        title=AUTHORED_BRAISED_CHICKEN.title,
    ),
)
PLANNER_AUTHORED_ANCHOR_CONCEPT_DICT = PLANNER_AUTHORED_ANCHOR_CONCEPT.model_dump(mode="json")

_PLANNER_COOKBOOK_ID = uuid.uuid4()
PLANNER_COOKBOOK_TARGET_STRICT_CONCEPT = DinnerConcept(
    free_text="Plan dinner using dishes from my market supper club cookbook.",
    guest_count=4,
    meal_type=MealType.DINNER,
    occasion=Occasion.DINNER_PARTY,
    dietary_restrictions=["No shellfish"],
    concept_source="planner_cookbook_target",
    planner_cookbook_target=PlannerLibraryCookbookTarget(
        cookbook_id=_PLANNER_COOKBOOK_ID,
        name="Market Supper Club",
        description="Late-summer dinner drafts.",
        mode=PlannerLibraryCookbookPlanningMode.STRICT,
    ),
)
PLANNER_COOKBOOK_TARGET_STRICT_CONCEPT_DICT = PLANNER_COOKBOOK_TARGET_STRICT_CONCEPT.model_dump(mode="json")
PLANNER_COOKBOOK_TARGET_BIASED_CONCEPT = PLANNER_COOKBOOK_TARGET_STRICT_CONCEPT.model_copy(
    update={
        "planner_cookbook_target": PlannerLibraryCookbookTarget(
            cookbook_id=_PLANNER_COOKBOOK_ID,
            name="Market Supper Club",
            description="Late-summer dinner drafts.",
            mode=PlannerLibraryCookbookPlanningMode.COOKBOOK_BIASED,
        )
    }
)
PLANNER_COOKBOOK_TARGET_BIASED_CONCEPT_DICT = PLANNER_COOKBOOK_TARGET_BIASED_CONCEPT.model_dump(mode="json")

KITCHEN_CONFIG = {
    "max_burners": 4,
    "max_oven_racks": 2,
    "has_second_oven": False,
}


def _make_state(merged_dag=None, errors=None, concept=None):
    """Build a minimal GRASPState dict for renderer tests."""
    return {
        "concept": concept or CONCEPT_DICT,
        "kitchen_config": KITCHEN_CONFIG,
        "merged_dag": merged_dag,
        "errors": errors or [],
    }


def _authored_record(payload: dict | None = None) -> AuthoredRecipeRecord:
    payload = payload or AUTHORED_BRAISED_CHICKEN.model_dump(mode="python")
    base = AuthoredRecipeCreate.model_validate(AUTHORED_BRAISED_CHICKEN.model_dump(mode="python"))
    return AuthoredRecipeRecord(
        recipe_id=_AUTHORED_RECIPE_ID,
        user_id=payload.get("user_id", base.user_id),
        cookbook_id=payload.get("cookbook_id", base.cookbook_id),
        title=payload.get("title", base.title),
        description=payload.get("description", base.description),
        cuisine=payload.get("cuisine", base.cuisine),
        authored_payload=payload,
    )


def _cookbook_authored_payload(title: str, description: str, cuisine: str) -> dict:
    payload = AUTHORED_BRAISED_CHICKEN.model_dump(mode="python")
    payload["title"] = title
    payload["description"] = description
    payload["cuisine"] = cuisine
    payload["cookbook_id"] = _PLANNER_COOKBOOK_ID
    payload["steps"] = [
        {
            **step,
            "dependencies": [
                {
                    **dependency,
                    "step_id": dependency["step_id"].replace(
                        "braised_chicken_with_saffron_onions",
                        title.lower().replace(" ", "_").replace("-", "_"),
                    ),
                }
                for dependency in step.get("dependencies", [])
            ],
        }
        for step in payload["steps"]
    ]
    return payload


def _cookbook_authored_record(
    *,
    recipe_id: uuid.UUID,
    title: str,
    updated_at,
    payload: dict | None = None,
) -> AuthoredRecipeRecord:
    payload = payload or _cookbook_authored_payload(title, AUTHORED_BRAISED_CHICKEN.description, AUTHORED_BRAISED_CHICKEN.cuisine)
    payload.setdefault("cookbook_id", _PLANNER_COOKBOOK_ID)
    return AuthoredRecipeRecord(
        recipe_id=recipe_id,
        user_id=payload["user_id"],
        cookbook_id=payload["cookbook_id"],
        title=title,
        description=payload["description"],
        cuisine=payload["cuisine"],
        authored_payload=payload,
        updated_at=updated_at,
    )


class TestCookbookGeneratorSeeding:
    @pytest.mark.asyncio
    async def test_cookbook_recipe_generator_skips_llm_and_builds_raw_recipe(self):
        state = {
            "concept": COOKBOOK_CONCEPT_DICT,
            "kitchen_config": KITCHEN_CONFIG,
            "equipment": [],
            "errors": [],
        }

        with patch("app.graph.nodes.generator._create_llm") as create_llm:
            result = await recipe_generator_node(state)

        create_llm.assert_not_called()
        assert "errors" not in result
        assert len(result["raw_recipes"]) == 1

        raw_recipe = result["raw_recipes"][0]
        assert raw_recipe["name"] == "Roast Chicken with Bread Salad"
        assert raw_recipe["servings"] == 4
        assert raw_recipe["cuisine"] == "Cookbook: The French Laundry Cookbook"
        assert raw_recipe["provenance"] == {
            "kind": "library_cookbook",
            "source_label": "The French Laundry Cookbook",
            "recipe_id": None,
            "cookbook_id": str(COOKBOOK_CONCEPT_DICT["selected_recipes"][0]["book_id"]),
        }
        assert len(raw_recipe["ingredients"]) == 3
        assert len(raw_recipe["steps"]) == 3
        assert raw_recipe["steps"][0].startswith("Season the chicken")

    @pytest.mark.asyncio
    async def test_cookbook_recipe_generator_returns_structured_validation_error_for_unparseable_chunk(self):
        broken_state = {
            "concept": DinnerConcept(
                free_text="Cookbook-selected recipes.",
                guest_count=2,
                meal_type=MealType.DINNER,
                occasion=Occasion.CASUAL,
                concept_source="cookbook",
                selected_recipes=[
                    SelectedCookbookRecipe(
                        chunk_id=uuid.uuid4(),
                        book_id=uuid.uuid4(),
                        book_title="Broken Cookbook",
                        text="Only a title and no method section",
                        chapter="Oops",
                        page_number=12,
                    )
                ],
            ).model_dump(mode="json"),
            "kitchen_config": KITCHEN_CONFIG,
            "equipment": [],
            "errors": [],
        }

        with patch("app.graph.nodes.generator._create_llm") as create_llm:
            result = await recipe_generator_node(broken_state)

        create_llm.assert_not_called()
        assert result["raw_recipes"] == []
        assert len(result["errors"]) == 1
        error = result["errors"][0]
        assert error["node_name"] == "recipe_generator"
        assert error["error_type"] == ErrorType.VALIDATION_FAILURE.value
        assert error["recoverable"] is False
        assert "did not contain at least 3 method steps" in error["message"]

    def test_build_cookbook_raw_recipes_rejects_unparseable_chunk(self):
        concept = DinnerConcept(
            free_text="Cookbook-selected recipes.",
            guest_count=2,
            meal_type=MealType.DINNER,
            occasion=Occasion.CASUAL,
            concept_source="cookbook",
            selected_recipes=[
                SelectedCookbookRecipe(
                    chunk_id=uuid.uuid4(),
                    book_id=uuid.uuid4(),
                    book_title="Broken Cookbook",
                    text="Only a title and no method section",
                    chapter="Oops",
                    page_number=12,
                )
            ],
        )

        with pytest.raises(ValueError, match="did not contain at least 3 method steps"):
            build_cookbook_raw_recipes(concept)


class TestAuthoredGeneratorSeeding:
    @pytest.mark.asyncio
    async def test_authored_recipe_generator_skips_llm_and_builds_raw_recipe(self):
        state = {
            "concept": AUTHORED_CONCEPT_DICT,
            "kitchen_config": KITCHEN_CONFIG,
            "equipment": [],
            "errors": [],
        }

        with (
            patch("app.graph.nodes.generator._create_llm") as create_llm,
            patch("app.graph.nodes.generator._load_authored_recipe_record", new=AsyncMock(return_value=_authored_record())),
        ):
            result = await recipe_generator_node(state)

        create_llm.assert_not_called()
        assert "errors" not in result
        assert len(result["raw_recipes"]) == 1

        raw_recipe = result["raw_recipes"][0]
        assert raw_recipe["name"] == AUTHORED_BRAISED_CHICKEN.title
        assert raw_recipe["servings"] == 4
        assert raw_recipe["cuisine"] == AUTHORED_BRAISED_CHICKEN.cuisine
        assert raw_recipe["provenance"] == {
            "kind": "library_authored",
            "source_label": AUTHORED_BRAISED_CHICKEN.title,
            "recipe_id": str(_AUTHORED_RECIPE_ID),
            "cookbook_id": None,
        }
        assert raw_recipe["estimated_total_minutes"] == 72
        assert raw_recipe["ingredients"][0]["name"] == "chicken leg quarters"
        assert raw_recipe["steps"][0].startswith("Brown the chicken. Sear skin-side down")
        assert "Target temp: 155F" in raw_recipe["steps"][0]
        assert "Yield: Forms the braising liquor and onion garnish." in raw_recipe["steps"][1]
        assert "Until: The leg joint yields easily when nudged." in raw_recipe["steps"][2]

    @pytest.mark.asyncio
    async def test_authored_recipe_generator_returns_structured_validation_error_for_malformed_payload(self):
        broken_payload = AUTHORED_BRAISED_CHICKEN.model_dump(mode="python")
        broken_payload["ingredients"] = []
        state = {
            "concept": AUTHORED_CONCEPT_DICT,
            "kitchen_config": KITCHEN_CONFIG,
            "equipment": [],
            "errors": [],
        }

        with (
            patch("app.graph.nodes.generator._create_llm") as create_llm,
            patch(
                "app.graph.nodes.generator._load_authored_recipe_record",
                new=AsyncMock(return_value=_authored_record(broken_payload)),
            ),
        ):
            result = await recipe_generator_node(state)

        create_llm.assert_not_called()
        assert result["raw_recipes"] == []
        assert len(result["errors"]) == 1
        error = result["errors"][0]
        assert error["node_name"] == "recipe_generator"
        assert error["error_type"] == ErrorType.VALIDATION_FAILURE.value
        assert error["recoverable"] is False
        assert str(_AUTHORED_RECIPE_ID) in error["message"]
        assert "could not compile into a scheduling input" in error["message"]
        assert "ingredients must contain at least one item" in error["message"]

    @pytest.mark.asyncio
    async def test_build_authored_raw_recipes_returns_single_compiled_recipe(self):
        concept = DinnerConcept.model_validate(AUTHORED_CONCEPT_DICT)

        with patch("app.graph.nodes.generator._load_authored_recipe_record", new=AsyncMock(return_value=_authored_record())):
            recipes = await build_authored_raw_recipes(concept)

        assert len(recipes) == 1
        assert recipes[0].name == AUTHORED_BRAISED_CHICKEN.title
        assert recipes[0].provenance == RecipeProvenance(
            kind="library_authored",
            source_label=AUTHORED_BRAISED_CHICKEN.title,
            recipe_id=str(_AUTHORED_RECIPE_ID),
            cookbook_id=None,
        )
        assert recipes[0].estimated_total_minutes == 72


class TestPlannerAuthoredGeneratorSeeding:
    @pytest.mark.asyncio
    async def test_build_planner_authored_anchor_raw_recipes_returns_single_compiled_recipe(self):
        concept = DinnerConcept.model_validate(PLANNER_AUTHORED_ANCHOR_CONCEPT_DICT)

        with patch("app.graph.nodes.generator._load_authored_recipe_record", new=AsyncMock(return_value=_authored_record())):
            recipes = await build_planner_authored_anchor_raw_recipes(concept)

        assert len(recipes) == 1
        assert recipes[0].name == AUTHORED_BRAISED_CHICKEN.title
        assert recipes[0].provenance == RecipeProvenance(
            kind="library_authored",
            source_label=AUTHORED_BRAISED_CHICKEN.title,
            recipe_id=str(_AUTHORED_RECIPE_ID),
            cookbook_id=None,
        )
        assert recipes[0].estimated_total_minutes == 72

    @pytest.mark.asyncio
    async def test_planner_authored_recipe_generator_seeds_anchor_and_generates_only_missing_complements(self):
        state = {
            "concept": PLANNER_AUTHORED_ANCHOR_CONCEPT_DICT,
            "kitchen_config": KITCHEN_CONFIG,
            "equipment": [{"name": "Dutch oven", "category": "cookware", "unlocks_techniques": ["braise"]}],
            "errors": [],
        }
        complement_recipe = AUTHORED_BRAISED_CHICKEN.compile_raw_recipe().model_copy(
            update={
                "name": "Charred Chicory Salad",
                "description": "A bitter, warm salad to balance the braise.",
                "cuisine": "French",
                "estimated_total_minutes": 18,
            }
        )
        expected_complements = _derive_recipe_count(MealType.DINNER, Occasion.DINNER_PARTY) - 1
        mock_output = RecipeGenerationOutput(recipes=[complement_recipe] * expected_complements)
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = mock_output
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        with (
            patch("app.graph.nodes.generator._create_llm", return_value=mock_llm),
            patch("app.graph.nodes.generator._load_authored_recipe_record", new=AsyncMock(return_value=_authored_record())),
            patch(
                "app.graph.nodes.generator.extract_token_usage",
                return_value={"node_name": "recipe_generator", "input_tokens": 101, "output_tokens": 202},
            ),
        ):
            result = await recipe_generator_node(state)

        assert "errors" not in result
        assert len(result["raw_recipes"]) == expected_complements + 1
        anchor_recipe = result["raw_recipes"][0]
        assert anchor_recipe["name"] == AUTHORED_BRAISED_CHICKEN.title
        assert anchor_recipe["provenance"] == {
            "kind": "library_authored",
            "source_label": AUTHORED_BRAISED_CHICKEN.title,
            "recipe_id": str(_AUTHORED_RECIPE_ID),
            "cookbook_id": None,
        }
        assert all(recipe["name"] == "Charred Chicory Salad" for recipe in result["raw_recipes"][1:])
        assert all(recipe["provenance"]["kind"] == "generated" for recipe in result["raw_recipes"][1:])
        assert result["token_usage"] == [{"node_name": "recipe_generator", "input_tokens": 101, "output_tokens": 202}]

        mock_llm.with_structured_output.assert_called_once_with(RecipeGenerationOutput)
        mock_chain.ainvoke.assert_awaited_once()
        messages = mock_chain.ainvoke.await_args.args[0]
        system_prompt = messages[0].content
        human_prompt = messages[1].content
        assert f"Number of complementary courses to generate: {expected_complements}" in system_prompt
        assert f"Name: {AUTHORED_BRAISED_CHICKEN.title}" in system_prompt
        assert "Do not regenerate, rename, restate, or paraphrase the anchor recipe" in system_prompt
        assert f"Generate {expected_complements} complementary recipes around the anchored dish" in human_prompt
        assert AUTHORED_BRAISED_CHICKEN.title in human_prompt

    @pytest.mark.asyncio
    async def test_planner_authored_recipe_generator_returns_structured_validation_error_for_malformed_anchor_payload(self):
        broken_payload = AUTHORED_BRAISED_CHICKEN.model_dump(mode="python")
        broken_payload["ingredients"] = []
        state = {
            "concept": PLANNER_AUTHORED_ANCHOR_CONCEPT_DICT,
            "kitchen_config": KITCHEN_CONFIG,
            "equipment": [],
            "errors": [],
        }

        with (
            patch("app.graph.nodes.generator._create_llm") as create_llm,
            patch(
                "app.graph.nodes.generator._load_authored_recipe_record",
                new=AsyncMock(return_value=_authored_record(broken_payload)),
            ),
        ):
            result = await recipe_generator_node(state)

        create_llm.assert_not_called()
        assert result["raw_recipes"] == []
        assert len(result["errors"]) == 1
        error = result["errors"][0]
        assert error["node_name"] == "recipe_generator"
        assert error["error_type"] == ErrorType.VALIDATION_FAILURE.value
        assert error["recoverable"] is False
        assert str(_AUTHORED_RECIPE_ID) in error["message"]
        assert "could not compile into a scheduling input" in error["message"]
        assert "ingredients must contain at least one item" in error["message"]


class TestPlannerCookbookGeneratorSeeding:
    @pytest.mark.asyncio
    async def test_planner_cookbook_strict_seeds_deterministic_authored_recipes_without_llm(self):
        state = {
            "concept": PLANNER_COOKBOOK_TARGET_STRICT_CONCEPT_DICT,
            "kitchen_config": KITCHEN_CONFIG,
            "equipment": [],
            "errors": [],
        }
        older_id = uuid.uuid4()
        newer_id = uuid.uuid4()
        records = [
            _cookbook_authored_record(
                recipe_id=older_id,
                title="Olive Oil Cake",
                updated_at="2025-01-01T12:00:00",
                payload=_cookbook_authored_payload("Olive Oil Cake", "Tender citrus cake.", "Italian"),
            ),
            _cookbook_authored_record(
                recipe_id=newer_id,
                title="Braised Fennel",
                updated_at="2025-01-02T12:00:00",
                payload=_cookbook_authored_payload("Braised Fennel", "Jammy fennel wedges.", "French"),
            ),
        ]

        with (
            patch("app.graph.nodes.generator._create_llm") as create_llm,
            patch(
                "app.graph.nodes.generator._load_cookbook_authored_recipe_records",
                new=AsyncMock(return_value=records),
            ),
        ):
            result = await recipe_generator_node(state)

        create_llm.assert_not_called()
        assert "errors" not in result
        assert [recipe["name"] for recipe in result["raw_recipes"]] == ["Olive Oil Cake", "Braised Fennel"]
        assert [recipe["provenance"] for recipe in result["raw_recipes"]] == [
            {
                "kind": "library_authored",
                "source_label": "Olive Oil Cake",
                "recipe_id": str(older_id),
                "cookbook_id": str(_PLANNER_COOKBOOK_ID),
            },
            {
                "kind": "library_authored",
                "source_label": "Braised Fennel",
                "recipe_id": str(newer_id),
                "cookbook_id": str(_PLANNER_COOKBOOK_ID),
            },
        ]

    @pytest.mark.asyncio
    async def test_planner_cookbook_biased_seeds_cookbook_recipes_then_generates_missing_complements(self):
        state = {
            "concept": PLANNER_COOKBOOK_TARGET_BIASED_CONCEPT_DICT,
            "kitchen_config": KITCHEN_CONFIG,
            "equipment": [{"name": "Dutch oven", "category": "cookware", "unlocks_techniques": ["braise"]}],
            "errors": [],
        }
        seeded_records = [
            _cookbook_authored_record(
                recipe_id=uuid.uuid4(),
                title="Braised Fennel",
                updated_at="2025-01-03T12:00:00",
                payload=_cookbook_authored_payload("Braised Fennel", "Jammy fennel wedges.", "French"),
            ),
            _cookbook_authored_record(
                recipe_id=uuid.uuid4(),
                title="Olive Oil Cake",
                updated_at="2025-01-02T12:00:00",
                payload=_cookbook_authored_payload("Olive Oil Cake", "Tender citrus cake.", "Italian"),
            ),
        ]
        expected_complements = _derive_recipe_count(MealType.DINNER, Occasion.DINNER_PARTY) - len(seeded_records)
        complement_recipe = AUTHORED_BRAISED_CHICKEN.compile_raw_recipe().model_copy(
            update={
                "name": "Charred Chicory Salad",
                "description": "A bitter, warm salad to balance the cookbook dishes.",
                "cuisine": "French",
                "estimated_total_minutes": 18,
            }
        )
        mock_output = RecipeGenerationOutput(recipes=[complement_recipe] * expected_complements)
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = mock_output
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        with (
            patch("app.graph.nodes.generator._create_llm", return_value=mock_llm),
            patch(
                "app.graph.nodes.generator._load_cookbook_authored_recipe_records",
                new=AsyncMock(return_value=seeded_records),
            ),
            patch(
                "app.graph.nodes.generator.extract_token_usage",
                return_value={"node_name": "recipe_generator", "input_tokens": 111, "output_tokens": 222},
            ),
        ):
            result = await recipe_generator_node(state)

        assert "errors" not in result
        assert [recipe["name"] for recipe in result["raw_recipes"][:2]] == ["Braised Fennel", "Olive Oil Cake"]
        assert [recipe["provenance"] for recipe in result["raw_recipes"][:2]] == [
            {
                "kind": "library_authored",
                "source_label": "Braised Fennel",
                "recipe_id": str(seeded_records[0].recipe_id),
                "cookbook_id": str(_PLANNER_COOKBOOK_ID),
            },
            {
                "kind": "library_authored",
                "source_label": "Olive Oil Cake",
                "recipe_id": str(seeded_records[1].recipe_id),
                "cookbook_id": str(_PLANNER_COOKBOOK_ID),
            },
        ]
        assert all(recipe["name"] == "Charred Chicory Salad" for recipe in result["raw_recipes"][2:])
        assert all(recipe["provenance"]["kind"] == "generated" for recipe in result["raw_recipes"][2:])
        assert len(result["raw_recipes"]) == len(seeded_records) + expected_complements
        assert result["token_usage"] == [{"node_name": "recipe_generator", "input_tokens": 111, "output_tokens": 222}]
        mock_llm.with_structured_output.assert_called_once_with(RecipeGenerationOutput)
        messages = mock_chain.ainvoke.await_args.args[0]
        system_prompt = messages[0].content
        human_prompt = messages[1].content
        assert f"Number of complementary courses to generate: {expected_complements}" in system_prompt
        assert "Name: Braised Fennel" in system_prompt
        assert "Do not regenerate, rename, restate, or paraphrase the anchor recipe" in system_prompt
        assert "Generate 1 complementary recipes around the anchored dish 'Braised Fennel'" in human_prompt

    @pytest.mark.asyncio
    async def test_planner_cookbook_strict_returns_structured_validation_error_for_empty_cookbook(self):
        state = {
            "concept": PLANNER_COOKBOOK_TARGET_STRICT_CONCEPT_DICT,
            "kitchen_config": KITCHEN_CONFIG,
            "equipment": [],
            "errors": [],
        }

        with (
            patch("app.graph.nodes.generator._create_llm") as create_llm,
            patch(
                "app.graph.nodes.generator._load_cookbook_authored_recipe_records",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = await recipe_generator_node(state)

        create_llm.assert_not_called()
        assert result["raw_recipes"] == []
        error = result["errors"][0]
        assert error["error_type"] == ErrorType.VALIDATION_FAILURE.value
        assert "has no authored recipes available for runtime seeding" in error["message"]
        assert "Market Supper Club" in error["message"]

    @pytest.mark.asyncio
    async def test_planner_cookbook_strict_returns_structured_validation_error_when_all_authored_payloads_fail_to_compile(self):
        state = {
            "concept": PLANNER_COOKBOOK_TARGET_STRICT_CONCEPT_DICT,
            "kitchen_config": KITCHEN_CONFIG,
            "equipment": [],
            "errors": [],
        }
        broken_payload = {
            **AUTHORED_BRAISED_CHICKEN.model_dump(mode="python"),
            "title": "Broken Braise",
            "ingredients": [],
            "cookbook_id": _PLANNER_COOKBOOK_ID,
        }
        records = [
            _cookbook_authored_record(
                recipe_id=uuid.uuid4(),
                title="Broken Braise",
                updated_at="2025-01-03T12:00:00",
                payload=broken_payload,
            )
        ]

        with (
            patch("app.graph.nodes.generator._create_llm") as create_llm,
            patch(
                "app.graph.nodes.generator._load_cookbook_authored_recipe_records",
                new=AsyncMock(return_value=records),
            ),
        ):
            result = await recipe_generator_node(state)

        create_llm.assert_not_called()
        assert result["raw_recipes"] == []
        error = result["errors"][0]
        assert error["error_type"] == ErrorType.VALIDATION_FAILURE.value
        assert "did not contain any authored recipes that could compile into scheduling inputs" in error["message"]
        assert "ingredients must contain at least one item" in error["message"]


# ── Timeline Entry Construction ──────────────────────────────────────────────


class TestBuildTimelineEntry:
    def test_basic_step(self):
        """A simple step with no duration_max produces no heads_up."""
        step = ScheduledStep(
            step_id="test_step_1",
            recipe_name="Test Recipe",
            description="Do something.",
            resource=Resource.HANDS,
            duration_minutes=10,
            start_at_minute=0,
            end_at_minute=10,
        )
        entry = _build_timeline_entry(step)

        assert entry.time_offset_minutes == 0
        assert entry.label == "T+0"
        assert entry.step_id == "test_step_1"
        assert entry.recipe_name == "Test Recipe"
        assert entry.action == "Do something."
        assert entry.resource == Resource.HANDS
        assert entry.duration_minutes == 10
        assert entry.heads_up is None
        assert entry.is_prep_ahead is False
        assert entry.prep_ahead_window is None

    def test_variable_duration_heads_up(self):
        """Step with duration_max != duration_minutes produces heads_up."""
        step = ScheduledStep(
            step_id="bake_1",
            recipe_name="Fondant",
            description="Bake at 200°C.",
            resource=Resource.OVEN,
            duration_minutes=12,
            duration_max=14,
            start_at_minute=180,
            end_at_minute=192,
        )
        entry = _build_timeline_entry(step)

        assert entry.heads_up == "12–14 min depending on oven temperature and size"
        assert entry.duration_max == 14

    def test_variable_duration_stovetop_heads_up(self):
        """Non-OVEN step with duration_max uses resource-specific heads_up text."""
        step = ScheduledStep(
            step_id="simmer_1",
            recipe_name="Soup",
            description="Simmer until reduced.",
            resource=Resource.STOVETOP,
            duration_minutes=20,
            duration_max=30,
            start_at_minute=0,
            end_at_minute=20,
        )
        entry = _build_timeline_entry(step)
        assert entry.heads_up == "20–30 min depending on stovetop heat"

    def test_same_duration_no_heads_up(self):
        """Step with duration_max == duration_minutes produces no heads_up."""
        step = ScheduledStep(
            step_id="boil_1",
            recipe_name="Pasta",
            description="Boil pasta.",
            resource=Resource.STOVETOP,
            duration_minutes=10,
            duration_max=10,
            start_at_minute=0,
            end_at_minute=10,
        )
        entry = _build_timeline_entry(step)
        assert entry.heads_up is None

    def test_prep_ahead_step(self):
        """Prep-ahead step flags are preserved."""
        step = ScheduledStep(
            step_id="chill_1",
            recipe_name="Fondant",
            description="Chill ramekins.",
            resource=Resource.PASSIVE,
            duration_minutes=30,
            start_at_minute=55,
            end_at_minute=85,
            can_be_done_ahead=True,
            prep_ahead_window="up to 24 hours",
        )
        entry = _build_timeline_entry(step)

        assert entry.is_prep_ahead is True
        assert entry.prep_ahead_window == "up to 24 hours"

    def test_label_format(self):
        """Label is T+{start_at_minute}."""
        step = ScheduledStep(
            step_id="s1",
            recipe_name="R",
            description="D",
            resource=Resource.HANDS,
            duration_minutes=5,
            start_at_minute=45,
            end_at_minute=50,
        )
        entry = _build_timeline_entry(step)
        assert entry.label == "T+45"


class TestBuildTimeline:
    def test_full_timeline_length(self):
        """Full 3-recipe merged DAG produces 12 entries (10 day-of + 2 prep-ahead, unified)."""
        timeline = _build_timeline(MERGED_DAG_FULL)
        assert len(timeline) == 12
        prep_ahead_count = sum(1 for e in timeline if e.is_prep_ahead)
        assert prep_ahead_count == 2

    def test_two_recipe_timeline_length(self):
        """2-recipe merged DAG produces 7 entries (6 day-of + 1 prep-ahead, unified)."""
        timeline = _build_timeline(MERGED_DAG_TWO_RECIPE)
        assert len(timeline) == 7
        prep_ahead_count = sum(1 for e in timeline if e.is_prep_ahead)
        assert prep_ahead_count == 1

    def test_timeline_ordering_preserved(self):
        """Timeline entries are in chronological order (by start_at_minute)."""
        timeline = _build_timeline(MERGED_DAG_FULL)
        offsets = [e.time_offset_minutes for e in timeline]
        assert offsets == sorted(offsets)

    def test_prep_ahead_entries_retain_flag(self):
        """Prep-ahead entries retain is_prep_ahead=True flag (no longer have Prep label)."""
        timeline = _build_timeline(MERGED_DAG_FULL)
        prep_entries = [e for e in timeline if e.is_prep_ahead]
        assert len(prep_entries) == 2
        for entry in prep_entries:
            assert entry.is_prep_ahead is True
            # Label is T+{offset}, not "Prep" — prep-ahead entries stay in-line
            assert entry.label.startswith("T+")

    def test_fixture_timeline_matches(self):
        """Our deterministic _build_timeline matches the fixture timeline."""
        timeline = _build_timeline(MERGED_DAG_FULL)
        fixture_timeline = NATURAL_LANGUAGE_SCHEDULE_FULL.timeline

        assert len(timeline) == len(fixture_timeline)
        for built, fixture in zip(timeline, fixture_timeline):
            assert built.step_id == fixture.step_id
            assert built.time_offset_minutes == fixture.time_offset_minutes
            assert built.resource == fixture.resource
            assert built.recipe_name == fixture.recipe_name


class TestMergedPrepRendering:
    def test_merged_prep_rendering(self):
        """Merged prep step shows allocation breakdown in action text."""
        merged_step = ScheduledStep(
            step_id="merged_celery_diced_1",
            recipe_name="[merged]",
            description="Prep 4 cups diced celery",
            resource=Resource.HANDS,
            duration_minutes=8,
            start_at_minute=0,
            end_at_minute=8,
            merged_from=["short_ribs_prep_celery", "risotto_prep_celery"],
            allocation={
                "Braised Short Ribs": "3 cups",
                "Risotto": "1 cup",
            },
        )
        entry = _build_timeline_entry(merged_step)
        assert entry.action == "Prep 4 cups diced celery (3 cups for Braised Short Ribs, 1 cup for Risotto)"
        assert entry.step_id == "merged_celery_diced_1"
        assert entry.recipe_name == "[merged]"

    def test_non_merged_step_unchanged(self):
        """Non-merged step description remains unchanged."""
        regular_step = ScheduledStep(
            step_id="short_ribs_sear_1",
            recipe_name="Braised Short Ribs",
            description="Sear the short ribs until deeply browned",
            resource=Resource.STOVETOP,
            duration_minutes=12,
            start_at_minute=10,
            end_at_minute=22,
        )
        entry = _build_timeline_entry(regular_step)
        assert entry.action == "Sear the short ribs until deeply browned"
        assert entry.step_id == "short_ribs_sear_1"

    def test_merged_step_without_allocation_unchanged(self):
        """Merged step without allocation dict falls back to plain description."""
        merged_step_no_allocation = ScheduledStep(
            step_id="merged_onions_sliced_1",
            recipe_name="[merged]",
            description="Prep 2 cups sliced onions",
            resource=Resource.HANDS,
            duration_minutes=5,
            start_at_minute=0,
            end_at_minute=5,
            merged_from=["recipe_a_prep_onions", "recipe_b_prep_onions"],
            allocation={},  # empty allocation dict
        )
        entry = _build_timeline_entry(merged_step_no_allocation)
        assert entry.action == "Prep 2 cups sliced onions"

    def test_allocation_sorting_deterministic(self):
        """Allocation breakdown is sorted alphabetically by recipe name."""
        merged_step = ScheduledStep(
            step_id="merged_garlic_minced_1",
            recipe_name="[merged]",
            description="Prep 6 cloves minced garlic",
            resource=Resource.HANDS,
            duration_minutes=3,
            start_at_minute=0,
            end_at_minute=3,
            merged_from=["z_recipe_garlic", "a_recipe_garlic", "m_recipe_garlic"],
            allocation={
                "Z Recipe": "3 cloves",
                "A Recipe": "2 cloves",
                "M Recipe": "1 clove",
            },
        )
        entry = _build_timeline_entry(merged_step)
        # Should be sorted: A, M, Z
        assert entry.action == "Prep 6 cloves minced garlic (2 cloves for A Recipe, 1 clove for M Recipe, 3 cloves for Z Recipe)"


# ── Fallback Summary ────────────────────────────────────────────────────────


class TestFallbackSummary:
    def test_full_schedule_summary(self):
        """Fallback summary mentions all recipe names and total time."""
        summary = _fallback_summary(MERGED_DAG_FULL, [])
        assert "3 course(s)" in summary
        assert "Braised Short Ribs" in summary
        assert "Chocolate Fondant" in summary
        assert "Pommes Puree" in summary
        assert "3 hours 15 minutes" in summary

    def test_two_recipe_summary(self):
        """Fallback summary for 2-recipe schedule."""
        summary = _fallback_summary(MERGED_DAG_TWO_RECIPE, [])
        assert "2 course(s)" in summary
        assert "Braised Short Ribs" in summary
        assert "Pommes Puree" in summary

    def test_fallback_error_summary_no_errors(self):
        """No errors returns None."""
        assert _fallback_error_summary([]) is None

    def test_fallback_error_summary_with_recipe_name(self):
        """Error with recipe_name metadata mentions the dropped recipe."""
        errors = [
            {
                "node_name": "rag_enricher",
                "error_type": "rag_failure",
                "recoverable": True,
                "message": "Failed",
                "metadata": {"recipe_name": "Chocolate Fondant"},
            }
        ]
        summary = _fallback_error_summary(errors)
        assert "Chocolate Fondant" in summary

    def test_fallback_error_summary_no_recipe_name(self):
        """Error without recipe_name metadata gives generic message."""
        errors = [
            {
                "node_name": "validator",
                "error_type": "validation_failure",
                "recoverable": True,
                "message": "Failed",
                "metadata": {},
            }
        ]
        summary = _fallback_error_summary(errors)
        assert "1 recoverable error" in summary


# ── Prompt Builder ──────────────────────────────────────────────────────────


class TestBuildSummaryPrompt:
    def test_prompt_includes_concept(self):
        """Prompt includes dinner concept text."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        prompt = _build_summary_prompt(concept, MERGED_DAG_FULL, [])
        assert "short ribs" in prompt
        assert "dinner_party" in prompt
        assert "Guest count: 4" in prompt

    def test_prompt_includes_recipe_names(self):
        """Prompt includes all recipe names from the schedule."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        prompt = _build_summary_prompt(concept, MERGED_DAG_FULL, [])
        assert "Braised Short Ribs" in prompt
        assert "Chocolate Fondant" in prompt
        assert "Pommes Puree" in prompt

    def test_prompt_includes_total_duration(self):
        """Prompt includes total duration."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        prompt = _build_summary_prompt(concept, MERGED_DAG_FULL, [])
        assert "195 minutes" in prompt

    def test_prompt_with_errors(self):
        """Prompt with errors includes error section and instructions."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        errors = [
            {
                "node_name": "rag_enricher",
                "message": "Fondant enrichment failed",
                "metadata": {"recipe_name": "Chocolate Fondant"},
            }
        ]
        prompt = _build_summary_prompt(concept, MERGED_DAG_TWO_RECIPE, errors)
        assert "PIPELINE ERRORS" in prompt
        assert "Fondant enrichment failed" in prompt
        assert "error_summary" in prompt

    def test_prompt_without_errors(self):
        """Prompt without errors tells LLM to set error_summary to null."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        prompt = _build_summary_prompt(concept, MERGED_DAG_FULL, [])
        assert "Set to null (no errors occurred)" in prompt

    def test_prompt_includes_resource_warnings_when_present(self):
        """Prompt includes RESOURCE WARNINGS section when warnings exist."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        # Create a MergedDAG with resource warnings
        dag_with_warnings = MergedDAG(
            scheduled_steps=MERGED_DAG_FULL.scheduled_steps,
            total_duration_minutes=MERGED_DAG_FULL.total_duration_minutes,
            active_time_minutes=MERGED_DAG_FULL.active_time_minutes,
            resource_warnings=[
                "Recipe C Medium Roast's oven cooking will finish ~60 minutes after Recipe A Long Braise due to oven capacity. Consider starting Recipe C Medium Roast earlier if you have a second oven.",
                "Recipe D Quick Sear may need to juggle burners with Recipe B Sauce.",
            ],
        )
        prompt = _build_summary_prompt(concept, dag_with_warnings, [])
        assert "## RESOURCE WARNINGS" in prompt
        assert "scheduling constraints were detected" in prompt
        assert "Recipe C Medium Roast" in prompt
        assert "oven capacity" in prompt
        assert "Recipe D Quick Sear" in prompt
        assert "juggle burners" in prompt

    def test_prompt_excludes_resource_warnings_when_empty(self):
        """Prompt excludes RESOURCE WARNINGS section when no warnings exist."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        # MERGED_DAG_FULL has resource_warnings=[] by default
        prompt = _build_summary_prompt(concept, MERGED_DAG_FULL, [])
        assert "## RESOURCE WARNINGS" not in prompt
        assert "scheduling constraints were detected" not in prompt

    def test_prompt_with_warnings_mentions_workarounds_in_output_requirements(self):
        """OUTPUT REQUIREMENTS section mentions incorporating warnings when present."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        dag_with_warnings = MergedDAG(
            scheduled_steps=MERGED_DAG_FULL.scheduled_steps,
            total_duration_minutes=MERGED_DAG_FULL.total_duration_minutes,
            active_time_minutes=MERGED_DAG_FULL.active_time_minutes,
            resource_warnings=["Test warning about equipment constraints."],
        )
        prompt = _build_summary_prompt(concept, dag_with_warnings, [])
        # The output requirements should always mention workarounds since it's static text
        assert "equipment constraints" in prompt or "workarounds" in prompt


# ── Node Function ───────────────────────────────────────────────────────────


class TestOneOvenConflictRendererContract:
    def test_schedule_model_defaults_one_oven_conflict_for_legacy_payload(self):
        schedule = NaturalLanguageSchedule.model_validate(
            {
                "timeline": [
                    {
                        "time_offset_minutes": 0,
                        "label": "T+0",
                        "step_id": "oven_step",
                        "recipe_name": "Roast",
                        "action": "Roast until browned",
                        "resource": Resource.OVEN,
                        "duration_minutes": 25,
                        "oven_temp_f": 425,
                    }
                ],
                "prep_ahead_entries": [],
                "total_duration_minutes": 25,
                "summary": "Legacy schedule",
            }
        )

        assert schedule.one_oven_conflict.classification == "compatible"
        assert schedule.one_oven_conflict.remediation.suggested_actions == []

    def test_schedule_model_preserves_explicit_one_oven_conflict_payload(self):
        schedule = NaturalLanguageSchedule.model_validate(
            {
                "timeline": [
                    {
                        "time_offset_minutes": 0,
                        "label": "T+0",
                        "step_id": "a_step_1",
                        "recipe_name": "Recipe A",
                        "action": "Bake at 375F",
                        "resource": Resource.OVEN,
                        "duration_minutes": 60,
                        "oven_temp_f": 375,
                    },
                    {
                        "time_offset_minutes": 60,
                        "label": "T+60",
                        "step_id": "b_step_1",
                        "recipe_name": "Recipe B",
                        "action": "Bake at 450F",
                        "resource": Resource.OVEN,
                        "duration_minutes": 60,
                        "oven_temp_f": 450,
                    },
                ],
                "prep_ahead_entries": [],
                "total_duration_minutes": 120,
                "summary": "Typed oven conflict schedule",
                "one_oven_conflict": {
                    "classification": "resequence_required",
                    "temperature_gap_f": 75,
                    "affected_step_ids": ["a_step_1", "b_step_1"],
                    "remediation": {
                        "requires_resequencing": True,
                        "suggested_actions": ["Stagger the bakes."],
                    },
                },
            }
        )

        assert schedule.one_oven_conflict.classification == "resequence_required"
        assert schedule.one_oven_conflict.remediation.requires_resequencing is True


class TestScheduleRendererNode:
    @pytest.mark.asyncio
    async def test_happy_path_full(self):
        """Full 3-recipe schedule with mocked LLM produces valid NaturalLanguageSchedule."""
        mock_output = ScheduleSummaryOutput(
            summary="A three-course dinner party menu.",
            error_summary=None,
        )
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = mock_output
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        state = _make_state(
            merged_dag=MERGED_DAG_FULL.model_dump(),
        )

        with patch("app.graph.nodes.renderer._create_llm", return_value=mock_llm):
            result = await schedule_renderer_node(state)

        assert "schedule" in result
        assert "errors" not in result

        schedule = NaturalLanguageSchedule.model_validate(result["schedule"])
        assert len(schedule.timeline) == 12  # all entries unified (10 day-of + 2 prep-ahead)
        assert len(schedule.prep_ahead_entries) == 0  # empty — all entries in timeline
        assert sum(1 for e in schedule.timeline if e.is_prep_ahead) == 2
        assert schedule.total_duration_minutes == 195
        assert schedule.total_duration_minutes_max == 210
        assert schedule.active_time_minutes == 282
        assert schedule.summary == "A three-course dinner party menu."
        assert schedule.error_summary is None
        assert schedule.one_oven_conflict == MERGED_DAG_FULL.one_oven_conflict

    @pytest.mark.asyncio
    async def test_happy_path_with_errors(self):
        """2-recipe schedule (partial) with errors populates error_summary."""
        mock_output = ScheduleSummaryOutput(
            summary="A two-course dinner.",
            error_summary="Chocolate Fondant dropped due to enrichment failure.",
        )
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = mock_output
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        errors = [
            {
                "node_name": "rag_enricher",
                "error_type": "rag_failure",
                "recoverable": True,
                "message": "Enrichment failed for 'Chocolate Fondant'",
                "metadata": {"recipe_name": "Chocolate Fondant"},
            }
        ]
        state = _make_state(
            merged_dag=MERGED_DAG_TWO_RECIPE.model_dump(),
            errors=errors,
        )

        with patch("app.graph.nodes.renderer._create_llm", return_value=mock_llm):
            result = await schedule_renderer_node(state)

        schedule = NaturalLanguageSchedule.model_validate(result["schedule"])
        assert len(schedule.timeline) == 7  # all entries unified (6 day-of + 1 prep-ahead)
        assert len(schedule.prep_ahead_entries) == 0  # empty — all entries in timeline
        assert schedule.error_summary == "Chocolate Fondant dropped due to enrichment failure."
        assert schedule.one_oven_conflict == MERGED_DAG_TWO_RECIPE.one_oven_conflict

    @pytest.mark.asyncio
    async def test_no_merged_dag_fatal(self):
        """Missing merged_dag returns fatal error (shouldn't happen in practice)."""
        state = _make_state(merged_dag=None)
        result = await schedule_renderer_node(state)

        assert "errors" in result
        assert "schedule" not in result
        error = result["errors"][0]
        assert error["node_name"] == "schedule_renderer"
        assert error["recoverable"] is False

    @pytest.mark.asyncio
    async def test_llm_failure_recoverable(self):
        """LLM failure produces schedule with fallback summary + recoverable error."""
        mock_chain = AsyncMock()
        mock_chain.ainvoke.side_effect = Exception("API timeout")
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        state = _make_state(
            merged_dag=MERGED_DAG_FULL.model_dump(),
        )

        with patch("app.graph.nodes.renderer._create_llm", return_value=mock_llm):
            result = await schedule_renderer_node(state)

        # Should still have a schedule (with fallback summary)
        assert "schedule" in result
        schedule = NaturalLanguageSchedule.model_validate(result["schedule"])
        assert len(schedule.timeline) == 12  # all entries unified
        assert len(schedule.prep_ahead_entries) == 0
        assert "3 course(s)" in schedule.summary

        # Should have a recoverable error
        assert "errors" in result
        error = result["errors"][0]
        assert error["node_name"] == "schedule_renderer"
        assert error["error_type"] == ErrorType.LLM_PARSE_FAILURE.value
        assert error["recoverable"] is True

    @pytest.mark.asyncio
    async def test_llm_failure_with_existing_errors(self):
        """LLM failure with pre-existing errors uses fallback error_summary."""
        mock_chain = AsyncMock()
        mock_chain.ainvoke.side_effect = Exception("API timeout")
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        errors = [
            {
                "node_name": "rag_enricher",
                "error_type": "rag_failure",
                "recoverable": True,
                "message": "Failed",
                "metadata": {"recipe_name": "Chocolate Fondant"},
            }
        ]
        state = _make_state(
            merged_dag=MERGED_DAG_TWO_RECIPE.model_dump(),
            errors=errors,
        )

        with patch("app.graph.nodes.renderer._create_llm", return_value=mock_llm):
            result = await schedule_renderer_node(state)

        schedule = NaturalLanguageSchedule.model_validate(result["schedule"])
        assert "Chocolate Fondant" in schedule.error_summary

    @pytest.mark.asyncio
    async def test_invalid_merged_dag_fatal(self):
        """Corrupted merged_dag dict returns fatal error."""
        state = _make_state(merged_dag={"not_valid": True})
        result = await schedule_renderer_node(state)

        assert "errors" in result
        assert "schedule" not in result
        error = result["errors"][0]
        assert error["recoverable"] is False
        assert error["error_type"] == ErrorType.VALIDATION_FAILURE.value

    @pytest.mark.asyncio
    async def test_timeline_determinism(self):
        """Timeline entries are identical across multiple calls (no LLM involved)."""
        mock_output = ScheduleSummaryOutput(summary="Test.", error_summary=None)
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = mock_output
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        state = _make_state(merged_dag=MERGED_DAG_FULL.model_dump())

        with patch("app.graph.nodes.renderer._create_llm", return_value=mock_llm):
            result1 = await schedule_renderer_node(state)
            result2 = await schedule_renderer_node(state)

        s1 = NaturalLanguageSchedule.model_validate(result1["schedule"])
        s2 = NaturalLanguageSchedule.model_validate(result2["schedule"])

        for e1, e2 in zip(s1.timeline, s2.timeline):
            assert e1.step_id == e2.step_id
            assert e1.time_offset_minutes == e2.time_offset_minutes
            assert e1.action == e2.action
