"""
tests/test_oven_temp_conflict.py
Unit tests for oven temperature conflict detection in DAG merger.

Tests verify R026: specific conflict error messages with recipe names, temps, time ranges.
"""

import asyncio
from datetime import datetime

import pytest

from app.graph.nodes.dag_merger import _merge_dags, ResourceConflictError, dag_merger_node
from app.models.enums import Resource
from app.models.recipe import (
    EnrichedRecipe,
    Ingredient,
    RawRecipe,
    RecipeStep,
    ValidatedRecipe,
)
from app.models.scheduling import MergedDAG, NaturalLanguageSchedule, OneOvenConflictSummary, RecipeDAG


def test_legacy_merged_dag_payload_without_one_oven_conflict_still_validates():
    payload = {
        "scheduled_steps": [
            {
                "step_id": "legacy_step",
                "recipe_name": "Legacy Recipe",
                "description": "Bake until set",
                "resource": Resource.OVEN,
                "duration_minutes": 30,
                "start_at_minute": 0,
                "end_at_minute": 30,
                "oven_temp_f": 375,
            }
        ],
        "total_duration_minutes": 30,
        "resource_utilisation": {"oven": [[0, 30]]},
        "resource_warnings": [],
    }

    merged = MergedDAG.model_validate(payload)

    assert merged.one_oven_conflict.classification == "compatible"
    assert merged.one_oven_conflict.tolerance_f == 15
    assert merged.one_oven_conflict.remediation.requires_resequencing is False


def test_legacy_schedule_payload_without_one_oven_conflict_still_validates():
    payload = {
        "timeline": [
            {
                "time_offset_minutes": 0,
                "label": "T+0",
                "step_id": "legacy_step",
                "recipe_name": "Legacy Recipe",
                "action": "Bake until set",
                "resource": Resource.OVEN,
                "duration_minutes": 30,
                "oven_temp_f": 375,
            }
        ],
        "prep_ahead_entries": [],
        "total_duration_minutes": 30,
        "summary": "Legacy schedule",
        "error_summary": None,
    }

    schedule = NaturalLanguageSchedule.model_validate(payload)

    assert schedule.one_oven_conflict.classification == "compatible"
    assert schedule.one_oven_conflict.remediation.suggested_actions == []


class TestOneOvenConflictSummaryModel:
    def test_defaults_are_conservative_and_backward_compatible(self):
        summary = OneOvenConflictSummary()

        assert summary.classification == "compatible"
        assert summary.tolerance_f == 15
        assert summary.temperature_gap_f is None
        assert summary.remediation.requires_resequencing is False
        assert summary.remediation.suggested_actions == []

    def test_partial_remediation_payload_validates(self):
        summary = OneOvenConflictSummary.model_validate(
            {
                "classification": "resequence_required",
                "temperature_gap_f": 25,
                "remediation": {"requires_resequencing": True},
            }
        )

        assert summary.classification == "resequence_required"
        assert summary.temperature_gap_f == 25
        assert summary.remediation.requires_resequencing is True
        assert summary.remediation.suggested_actions == []
        assert summary.remediation.blocking_recipe_names == []

    def test_missing_classification_metadata_stays_compatible(self):
        summary = OneOvenConflictSummary.model_validate({"remediation": {"notes": "Legacy note"}})

        assert summary.classification == "compatible"
        assert summary.remediation.notes == "Legacy note"


class TestOvenConflictClassificationContract:
    def test_edge_tolerance_of_15f_is_compatible(self):
        summary = OneOvenConflictSummary.model_validate(
            {
                "classification": "compatible",
                "temperature_gap_f": 15,
                "affected_step_ids": ["a_step_1", "b_step_1"],
            }
        )

        assert summary.classification == "compatible"
        assert summary.temperature_gap_f == 15

    def test_second_oven_relaxes_conflict_to_compatible(self):
        summary = OneOvenConflictSummary.model_validate(
            {
                "classification": "compatible",
                "has_second_oven": True,
                "temperature_gap_f": 75,
                "blocking_recipe_names": ["Recipe A", "Recipe B"],
            }
        )

        assert summary.classification == "compatible"
        assert summary.has_second_oven is True
        assert summary.temperature_gap_f == 75

    def test_single_oven_different_temp_can_be_resequence_required(self):
        summary = OneOvenConflictSummary.model_validate(
            {
                "classification": "resequence_required",
                "has_second_oven": False,
                "temperature_gap_f": 75,
                "blocking_recipe_names": ["Recipe A", "Recipe B"],
                "affected_step_ids": ["a_step_1", "b_step_1"],
                "remediation": {
                    "requires_resequencing": True,
                    "suggested_actions": ["Bake Recipe B after Recipe A finishes."],
                    "delaying_recipe_names": ["Recipe B"],
                    "blocking_recipe_names": ["Recipe A"],
                },
            }
        )

        assert summary.classification == "resequence_required"
        assert summary.remediation.requires_resequencing is True
        assert summary.remediation.delaying_recipe_names == ["Recipe B"]

    def test_single_oven_irreconcilable_payload_is_typed(self):
        summary = OneOvenConflictSummary.model_validate(
            {
                "classification": "irreconcilable",
                "has_second_oven": False,
                "temperature_gap_f": 75,
                "blocking_recipe_names": ["Recipe A", "Recipe B"],
                "affected_step_ids": ["a_step_1", "b_step_1"],
                "remediation": {
                    "requires_resequencing": False,
                    "suggested_actions": ["Use a second oven or change recipes."],
                    "blocking_recipe_names": ["Recipe A", "Recipe B"],
                },
            }
        )

        assert summary.classification == "irreconcilable"
        assert summary.remediation.blocking_recipe_names == ["Recipe A", "Recipe B"]


class TestOvenTemperatureConflict:
    """Tests for oven temperature conflict detection (R026)."""

    def test_single_oven_serializes_conflicting_temps_without_serving_time(self):
        """Single oven serializes different temps in ASAP mode and reports resequencing metadata."""
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
        assert result.one_oven_conflict.classification == "resequence_required"
        assert result.one_oven_conflict.temperature_gap_f == 75
        assert result.one_oven_conflict.blocking_recipe_names == ["Recipe A", "Recipe B"]
        assert result.one_oven_conflict.affected_step_ids == ["a_step_1", "b_step_1"]
        assert result.one_oven_conflict.remediation.requires_resequencing is True
        assert result.one_oven_conflict.remediation.delaying_recipe_names == ["Recipe B"]
        assert result.one_oven_conflict.remediation.blocking_recipe_names == ["Recipe A"]
        assert result.one_oven_conflict.remediation.suggested_actions == ["Bake Recipe B after Recipe A finishes."]
        assert "Single-oven schedule remains feasible" in (result.one_oven_conflict.remediation.notes or "")

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
        assert result.one_oven_conflict.classification == "compatible"
        assert result.one_oven_conflict.has_second_oven is True
        assert result.one_oven_conflict.temperature_gap_f is None or result.one_oven_conflict.temperature_gap_f == 75

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
        assert result.one_oven_conflict.classification == "compatible"
        assert result.one_oven_conflict.temperature_gap_f == 10

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
        assert result.one_oven_conflict.classification == "compatible"
        assert result.one_oven_conflict.temperature_gap_f is None

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
        assert result.one_oven_conflict.classification == "resequence_required"
        assert result.one_oven_conflict.remediation.delaying_recipe_names


def test_dag_merger_node_typed_resource_conflict_metadata_on_irreconcilable_error():
    """Finish-together one-oven overlaps stay on the fatal resource-conflict rail with typed metadata."""
    state = {
        "recipe_dags": [
            {"recipe_name": "Recipe A", "recipe_slug": "recipe_a", "steps": [], "edges": []},
            {"recipe_name": "Recipe B", "recipe_slug": "recipe_b", "steps": [], "edges": []},
        ],
        "validated_recipes": [
            {
                "source": {
                    "source": {
                        "name": "Recipe A",
                        "description": "test",
                        "servings": 2,
                        "cuisine": "test",
                        "estimated_total_minutes": 60,
                        "ingredients": [{"name": "x", "quantity": "1"}],
                        "steps": ["Bake at 375"],
                    },
                    "steps": [
                        {
                            "step_id": "a_step_1",
                            "description": "Bake at 375F",
                            "duration_minutes": 60,
                            "resource": "oven",
                            "oven_temp_f": 375,
                        }
                    ],
                },
                "validated_at": datetime.now().isoformat(),
            },
            {
                "source": {
                    "source": {
                        "name": "Recipe B",
                        "description": "test",
                        "servings": 2,
                        "cuisine": "test",
                        "estimated_total_minutes": 60,
                        "ingredients": [{"name": "y", "quantity": "1"}],
                        "steps": ["Bake at 450"],
                    },
                    "steps": [
                        {
                            "step_id": "b_step_1",
                            "description": "Bake at 450F",
                            "duration_minutes": 60,
                            "resource": "oven",
                            "oven_temp_f": 450,
                        }
                    ],
                },
                "validated_at": datetime.now().isoformat(),
            },
        ],
        "kitchen_config": {"max_burners": 4, "has_second_oven": False},
        "concept": {"serving_time": "18:00"},
    }

    result = asyncio.run(dag_merger_node(state))

    assert "errors" in result
    error = result["errors"][0]
    assert error["error_type"] == "resource_conflict"
    assert error["recoverable"] is False
    metadata = error["metadata"]
    assert metadata["classification"] == "irreconcilable"
    assert metadata["temperature_gap_f"] == 75
    assert metadata["blocking_recipe_names"] == ["Recipe A", "Recipe B"]
    assert metadata["affected_step_ids"] == ["a_step_1", "b_step_1"]
    assert metadata["remediation"]["requires_resequencing"] is False
    assert metadata["remediation"]["suggested_actions"] == ["Use a second oven or change recipes."]
    assert "detail" in metadata


def test_merge_dags_finish_together_single_oven_returns_fatal_structured_conflict():
    raw_a = RawRecipe(
        name="Recipe A",
        description="test",
        servings=2,
        cuisine="test",
        estimated_total_minutes=60,
        ingredients=[Ingredient(name="x", quantity="1")],
        steps=["bake at 375"],
    )
    raw_b = RawRecipe(
        name="Recipe B",
        description="test",
        servings=2,
        cuisine="test",
        estimated_total_minutes=60,
        ingredients=[Ingredient(name="y", quantity="1")],
        steps=["bake at 450"],
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

    with pytest.raises(ResourceConflictError) as exc_info:
        _merge_dags(
            [dag_a, dag_b],
            [validated_a, validated_b],
            {"max_burners": 4, "has_second_oven": False},
            serving_time="18:00",
        )

    metadata = exc_info.value.metadata
    assert metadata["classification"] == "irreconcilable"
    assert metadata["temperature_gap_f"] == 75
    assert metadata["affected_step_ids"] == ["a_step_1", "b_step_1"]
    assert metadata["remediation"]["requires_resequencing"] is False


def test_single_oven_missing_temperature_stays_conservative_even_with_serving_time():
    raw_a = RawRecipe(
        name="Recipe A",
        description="test",
        servings=2,
        cuisine="test",
        estimated_total_minutes=60,
        ingredients=[Ingredient(name="x", quantity="1")],
        steps=["bake at 375"],
    )
    raw_b = RawRecipe(
        name="Recipe B",
        description="test",
        servings=2,
        cuisine="test",
        estimated_total_minutes=60,
        ingredients=[Ingredient(name="y", quantity="1")],
        steps=["keep warm"],
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
    enriched_b = EnrichedRecipe(
        source=raw_b,
        steps=[
            RecipeStep(
                step_id="b_step_1",
                description="keep warm in oven",
                duration_minutes=60,
                resource=Resource.OVEN,
                oven_temp_f=None,
            )
        ],
    )

    dag_a = RecipeDAG(recipe_name="Recipe A", recipe_slug="recipe_a", steps=[], edges=[])
    dag_b = RecipeDAG(recipe_name="Recipe B", recipe_slug="recipe_b", steps=[], edges=[])
    validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
    validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())

    result = _merge_dags(
        [dag_a, dag_b],
        [validated_a, validated_b],
        {"max_burners": 4, "has_second_oven": False},
        serving_time="18:00",
    )

    assert result.one_oven_conflict.classification == "compatible"
    assert result.one_oven_conflict.temperature_gap_f is None
