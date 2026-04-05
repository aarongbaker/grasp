"""
tests/test_oven_temp_conflict.py
Unit tests for oven temperature conflict detection in DAG merger.

Tests verify R026: specific conflict error messages with recipe names, temps, time ranges.
"""

from datetime import datetime

import pytest

from app.graph.nodes.dag_merger import _merge_dags, ResourceConflictError
from app.models.enums import Resource
from app.models.recipe import (
    EnrichedRecipe,
    Ingredient,
    RawRecipe,
    RecipeStep,
    ValidatedRecipe,
)
from app.models.scheduling import RecipeDAG


class TestOvenTemperatureConflict:
    """Tests for oven temperature conflict detection (R026)."""

    def test_single_oven_serializes_conflicting_temps(self):
        """Single oven serializes steps with different temps (no conflict error, just sequential)."""
        # Recipe A: 375°F
        raw_a = RawRecipe(
            name="Recipe A",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=60,
            ingredients=[Ingredient(name="x", quantity="1")],
            steps=["bake at 375"],
        )
        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(
                    step_id="a_step_1",
                    description="bake at 375F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=375,
                )
            ],
        )

        # Recipe B: 450°F (75°F difference)
        raw_b = RawRecipe(
            name="Recipe B",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=60,
            ingredients=[Ingredient(name="y", quantity="1")],
            steps=["bake at 450"],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(
                    step_id="b_step_1",
                    description="bake at 450F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=450,
                )
            ],
        )

        dag_a = RecipeDAG(recipe_name="Recipe A", recipe_slug="recipe_a", steps=[], edges=[])
        dag_b = RecipeDAG(recipe_name="Recipe B", recipe_slug="recipe_b", steps=[], edges=[])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())

        kitchen = {"max_burners": 4, "has_second_oven": False}

        # Should succeed — single oven capacity forces sequential execution
        result = _merge_dags([dag_a, dag_b], [validated_a, validated_b], kitchen)

        # Both steps should be scheduled sequentially
        assert len(result.scheduled_steps) == 2
        by_id = {s.step_id: s for s in result.scheduled_steps}
        
        # Steps should NOT overlap (sequential due to capacity=1)
        a_step = by_id["a_step_1"]
        b_step = by_id["b_step_1"]
        
        assert (a_step.end_at_minute <= b_step.start_at_minute or 
                b_step.end_at_minute <= a_step.start_at_minute), (
            f"Steps should be sequential: A [{a_step.start_at_minute},{a_step.end_at_minute}) "
            f"vs B [{b_step.start_at_minute},{b_step.end_at_minute})"
        )

    def test_dual_oven_allows_conflicting_temps(self):
        """With has_second_oven=True, different temps can run in parallel."""
        # Recipe A: 375°F
        raw_a = RawRecipe(
            name="Recipe A",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=60,
            ingredients=[Ingredient(name="x", quantity="1")],
            steps=["bake at 375"],
        )
        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(
                    step_id="a_step_1",
                    description="bake at 375F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=375,
                )
            ],
        )

        # Recipe B: 450°F
        raw_b = RawRecipe(
            name="Recipe B",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=60,
            ingredients=[Ingredient(name="y", quantity="1")],
            steps=["bake at 450"],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(
                    step_id="b_step_1",
                    description="bake at 450F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=450,
                )
            ],
        )

        dag_a = RecipeDAG(recipe_name="Recipe A", recipe_slug="recipe_a", steps=[], edges=[])
        dag_b = RecipeDAG(recipe_name="Recipe B", recipe_slug="recipe_b", steps=[], edges=[])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())

        kitchen = {"max_burners": 4, "has_second_oven": True}

        # Should succeed with dual oven
        result = _merge_dags([dag_a, dag_b], [validated_a, validated_b], kitchen)

        # Both steps should be scheduled (parallel)
        assert len(result.scheduled_steps) == 2
        by_id = {s.step_id: s for s in result.scheduled_steps}
        
        # Both should start at T+0 (parallel execution with 2 ovens)
        assert by_id["a_step_1"].start_at_minute == 0
        assert by_id["b_step_1"].start_at_minute == 0

    def test_similar_temps_allowed(self):
        """Temps within 15°F tolerance are allowed (e.g., 375 and 385)."""
        # Recipe A: 375°F
        raw_a = RawRecipe(
            name="Recipe A",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=60,
            ingredients=[Ingredient(name="x", quantity="1")],
            steps=["bake at 375"],
        )
        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(
                    step_id="a_step_1",
                    description="bake at 375F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=375,
                )
            ],
        )

        # Recipe B: 385°F (10°F difference, within tolerance)
        raw_b = RawRecipe(
            name="Recipe B",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=60,
            ingredients=[Ingredient(name="y", quantity="1")],
            steps=["bake at 385"],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(
                    step_id="b_step_1",
                    description="bake at 385F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=385,
                )
            ],
        )

        dag_a = RecipeDAG(recipe_name="Recipe A", recipe_slug="recipe_a", steps=[], edges=[])
        dag_b = RecipeDAG(recipe_name="Recipe B", recipe_slug="recipe_b", steps=[], edges=[])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())

        kitchen = {"max_burners": 4, "has_second_oven": False}

        # Should succeed — temps are similar enough
        result = _merge_dags([dag_a, dag_b], [validated_a, validated_b], kitchen)

        # Both steps should be scheduled
        assert len(result.scheduled_steps) == 2

    def test_none_temp_no_conflict(self):
        """Steps with oven_temp_f=None don't trigger conflict detection."""
        # Recipe A: 375°F
        raw_a = RawRecipe(
            name="Recipe A",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=60,
            ingredients=[Ingredient(name="x", quantity="1")],
            steps=["bake"],
        )
        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(
                    step_id="a_step_1",
                    description="bake at 375F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=375,
                )
            ],
        )

        # Recipe B: no temp specified
        raw_b = RawRecipe(
            name="Recipe B",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=60,
            ingredients=[Ingredient(name="y", quantity="1")],
            steps=["keep warm"],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(
                    step_id="b_step_1",
                    description="keep warm in oven",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=None,  # No temp specified
                )
            ],
        )

        dag_a = RecipeDAG(recipe_name="Recipe A", recipe_slug="recipe_a", steps=[], edges=[])
        dag_b = RecipeDAG(recipe_name="Recipe B", recipe_slug="recipe_b", steps=[], edges=[])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())

        kitchen = {"max_burners": 4, "has_second_oven": False}

        # Should succeed — no conflict when one temp is None
        result = _merge_dags([dag_a, dag_b], [validated_a, validated_b], kitchen)

        # Both steps should be scheduled (sequentially due to single oven)
        assert len(result.scheduled_steps) == 2

    def test_sequential_different_temps_allowed(self):
        """Different temps are OK if steps don't overlap (sequential)."""
        # Recipe A: 375°F
        raw_a = RawRecipe(
            name="Recipe A",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=120,
            ingredients=[Ingredient(name="x", quantity="1")],
            steps=["prep", "bake at 375"],
        )
        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(
                    step_id="a_step_1",
                    description="prep",
                    duration_minutes=10,
                    resource=Resource.HANDS,
                ),
                RecipeStep(
                    step_id="a_step_2",
                    description="bake at 375F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=375,
                    depends_on=["a_step_1"],
                ),
            ],
        )

        # Recipe B: 450°F, starts after prep
        raw_b = RawRecipe(
            name="Recipe B",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=70,
            ingredients=[Ingredient(name="y", quantity="1")],
            steps=["prep", "bake at 450"],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(
                    step_id="b_step_1",
                    description="prep",
                    duration_minutes=10,
                    resource=Resource.HANDS,
                ),
                RecipeStep(
                    step_id="b_step_2",
                    description="bake at 450F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=450,
                    depends_on=["b_step_1"],
                ),
            ],
        )

        dag_a = RecipeDAG(recipe_name="Recipe A", recipe_slug="recipe_a", steps=[], edges=[("a_step_1", "a_step_2")])
        dag_b = RecipeDAG(recipe_name="Recipe B", recipe_slug="recipe_b", steps=[], edges=[("b_step_1", "b_step_2")])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())

        kitchen = {"max_burners": 4, "has_second_oven": False}

        # Should succeed — with single oven, steps will be scheduled sequentially due to capacity
        result = _merge_dags([dag_a, dag_b], [validated_a, validated_b], kitchen)

        # All 4 steps should be scheduled
        assert len(result.scheduled_steps) == 4
        by_id = {s.step_id: s for s in result.scheduled_steps}
        
        # Oven steps should not overlap (sequential due to single oven capacity)
        a_oven = by_id["a_step_2"]
        b_oven = by_id["b_step_2"]
        
        # Check they don't overlap
        assert (a_oven.end_at_minute <= b_oven.start_at_minute or 
                b_oven.end_at_minute <= a_oven.start_at_minute), (
            f"Oven steps should not overlap: A [{a_oven.start_at_minute},{a_oven.end_at_minute}) "
            f"vs B [{b_oven.start_at_minute},{b_oven.end_at_minute})"
        )
