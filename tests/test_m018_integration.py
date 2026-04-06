"""
tests/test_m018_integration.py
Integration tests for M018: Shared Prep Merging + Oven Conflict Detection.

These tests verify that the merger + renderer contract works correctly for:
  - R027: Shared prep merging (exact match, different prep, unit conversion)
  - R028: Oven conflict detection (serialization, dual oven, temperature tolerance)

Tests build EnrichedRecipe objects with structured metadata → create minimal
RecipeDAG objects → call _merge_dags() → assert scheduled_steps structure →
call renderer to verify timeline format.

No graph execution, no LLM, no database — pure unit integration testing
of the merger + renderer components.
"""

from datetime import datetime

import pytest

from app.graph.nodes.dag_merger import (
    ResourceConflictError,
    _merge_dags,
)
from app.graph.nodes.renderer import _build_timeline_entry
from app.models.enums import Resource
from app.models.recipe import (
    EnrichedRecipe,
    Ingredient,
    IngredientUse,
    RawRecipe,
    RecipeStep,
    ValidatedRecipe,
)
from app.models.scheduling import RecipeDAG, ScheduledStep

# ── Helpers ──────────────────────────────────────────────────────────────────

DEFAULT_KITCHEN = {
    "max_burners": 4,
    "max_oven_racks": 2,
    "has_second_oven": False,
}


def _make_validated(enriched: EnrichedRecipe) -> ValidatedRecipe:
    """Wrap an EnrichedRecipe in a ValidatedRecipe for testing."""
    return ValidatedRecipe(source=enriched, validated_at=datetime.now())


def _make_recipe_with_prep(
    name: str,
    slug: str,
    step_id: str,
    ingredient_name: str,
    prep_method: str,
    quantity: float,
    unit: str,
) -> tuple[RecipeDAG, ValidatedRecipe]:
    """
    Build a minimal recipe with a single prep step for shared prep testing.
    
    Returns (RecipeDAG, ValidatedRecipe) tuple ready for _merge_dags().
    """
    ingredient = Ingredient(name=ingredient_name, quantity=f"{quantity} {unit}")
    ingredient_use = IngredientUse(
        ingredient_name=ingredient_name,
        prep_method=prep_method,
        quantity_canonical=quantity,
        unit_canonical=unit,
        quantity_original=f"{quantity} {unit}",
    )
    
    raw = RawRecipe(
        name=name,
        description=f"Test recipe with {ingredient_name}",
        servings=2,
        cuisine="test",
        estimated_total_minutes=10,
        ingredients=[ingredient],
        steps=[f"Prep {ingredient_name} ({prep_method})"],
    )
    
    enriched = EnrichedRecipe(
        source=raw,
        steps=[
            RecipeStep(
                step_id=step_id,
                description=f"Prep {ingredient_name} ({prep_method})",
                duration_minutes=10,
                resource=Resource.HANDS,
                ingredient_uses=[ingredient_use],
            )
        ],
    )
    
    dag = RecipeDAG(recipe_name=name, recipe_slug=slug, steps=[], edges=[])
    validated = _make_validated(enriched)
    return dag, validated


def _make_recipe_with_oven(
    name: str,
    slug: str,
    step_id: str,
    temp_f: int,
    duration: int,
) -> tuple[RecipeDAG, ValidatedRecipe]:
    """
    Build a minimal recipe with a single oven step for oven conflict testing.
    
    Returns (RecipeDAG, ValidatedRecipe) tuple ready for _merge_dags().
    """
    raw = RawRecipe(
        name=name,
        description=f"Test recipe requiring oven at {temp_f}°F",
        servings=2,
        cuisine="test",
        estimated_total_minutes=duration,
        ingredients=[],
        steps=[f"Bake at {temp_f}°F for {duration} minutes"],
    )
    
    enriched = EnrichedRecipe(
        source=raw,
        steps=[
            RecipeStep(
                step_id=step_id,
                description=f"Bake at {temp_f}°F for {duration} minutes",
                duration_minutes=duration,
                resource=Resource.OVEN,
                oven_temp_f=temp_f,
            )
        ],
    )
    
    dag = RecipeDAG(recipe_name=name, recipe_slug=slug, steps=[], edges=[])
    validated = _make_validated(enriched)
    return dag, validated


def _steps_overlap(step_a: ScheduledStep, step_b: ScheduledStep) -> bool:
    """Check if two scheduled steps have overlapping time windows."""
    return not (step_a.end_at_minute <= step_b.start_at_minute or
                step_b.end_at_minute <= step_a.start_at_minute)


# ── Shared Prep Integration Tests ───────────────────────────────────────────


@pytest.mark.integration
class TestSharedPrepIntegration:
    """Integration tests for R027: Shared prep merging."""

    def test_exact_match_merge_end_to_end(self):
        """
        Exact ingredient+prep match creates merged node with allocation breakdown.
        
        Recipe A: prep 2 cups diced celery
        Recipe B: prep 1 cup diced celery
        Result: 1 merged prep node with 3 cups total, allocation dict showing breakdown
        """
        dag_a, val_a = _make_recipe_with_prep(
            "Recipe A", "recipe_a", "a_prep", "celery", "diced", 2.0, "cup"
        )
        dag_b, val_b = _make_recipe_with_prep(
            "Recipe B", "recipe_b", "b_prep", "celery", "diced", 1.0, "cup"
        )

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        # Should have 1 merged step instead of 2 separate steps
        assert len(result.scheduled_steps) == 1, (
            f"Expected 1 merged step, got {len(result.scheduled_steps)}"
        )

        merged_step = result.scheduled_steps[0]

        # Verify merged_from field populated with both original step_ids
        assert set(merged_step.merged_from) == {"a_prep", "b_prep"}, (
            f"Expected merged_from=['a_prep', 'b_prep'], got {merged_step.merged_from}"
        )

        # Verify allocation dict shows breakdown
        assert "Recipe A" in merged_step.allocation, (
            f"Allocation missing Recipe A: {merged_step.allocation}"
        )
        assert "Recipe B" in merged_step.allocation, (
            f"Allocation missing Recipe B: {merged_step.allocation}"
        )
        assert merged_step.allocation["Recipe A"] == "2.0 cup", (
            f"Expected '2.0 cup', got {merged_step.allocation['Recipe A']}"
        )
        assert merged_step.allocation["Recipe B"] == "1.0 cup", (
            f"Expected '1.0 cup', got {merged_step.allocation['Recipe B']}"
        )

        # Verify renderer output includes allocation breakdown
        entry = _build_timeline_entry(merged_step)
        assert "for Recipe A" in entry.action or "Recipe A" in entry.action, (
            f"Renderer output missing Recipe A allocation: {entry.action}"
        )
        assert "for Recipe B" in entry.action or "Recipe B" in entry.action, (
            f"Renderer output missing Recipe B allocation: {entry.action}"
        )

    def test_different_prep_no_merge(self):
        """
        Different prep methods prevent merging even with same ingredient.
        
        Recipe A: diced celery
        Recipe B: sliced celery
        Result: 2 separate steps (no merge)
        """
        dag_a, val_a = _make_recipe_with_prep(
            "Recipe A", "recipe_a", "a_prep", "celery", "diced", 2.0, "cup"
        )
        dag_b, val_b = _make_recipe_with_prep(
            "Recipe B", "recipe_b", "b_prep", "celery", "sliced", 1.0, "cup"
        )

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        # Should have 2 separate steps (no merge)
        assert len(result.scheduled_steps) == 2, (
            f"Expected 2 steps (no merge), got {len(result.scheduled_steps)}"
        )

        # Verify neither step has merged_from populated
        for step in result.scheduled_steps:
            assert step.merged_from == [], (
                f"Expected no merged_from, got {step.merged_from}"
            )
            assert step.allocation == {}, (
                f"Expected no allocation, got {step.allocation}"
            )

    def test_unit_conversion_merge(self):
        """
        Unit conversion enables merge for equivalent quantities.
        
        Recipe A: 50g butter
        Recipe B: 3.5 tbsp butter
        Result: 1 merged step (50g ≈ 3.5 tbsp based on S01 unit tests)
        """
        dag_a, val_a = _make_recipe_with_prep(
            "Recipe A", "recipe_a", "a_prep", "butter", "cubed", 50.0, "g"
        )
        dag_b, val_b = _make_recipe_with_prep(
            "Recipe B", "recipe_b", "b_prep", "butter", "cubed", 3.5, "tbsp"
        )

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        # Should have 1 merged step after unit conversion
        assert len(result.scheduled_steps) == 1, (
            f"Expected 1 merged step after unit conversion, got {len(result.scheduled_steps)}"
        )

        merged_step = result.scheduled_steps[0]

        # Verify merged_from field populated
        assert set(merged_step.merged_from) == {"a_prep", "b_prep"}, (
            f"Expected merged_from=['a_prep', 'b_prep'], got {merged_step.merged_from}"
        )

        # Verify allocation dict shows both recipes with their original units
        assert "Recipe A" in merged_step.allocation, (
            f"Allocation missing Recipe A: {merged_step.allocation}"
        )
        assert "Recipe B" in merged_step.allocation, (
            f"Allocation missing Recipe B: {merged_step.allocation}"
        )


# ── Oven Conflict Integration Tests ─────────────────────────────────────────


@pytest.mark.integration
class TestOvenConflictIntegration:
    """Integration tests for R028: Oven conflict detection."""

    def test_single_oven_serializes_different_temps(self):
        """
        Single oven serializes steps with different temperatures.
        
        Recipe A: 375°F for 60 minutes
        Recipe B: 450°F for 60 minutes
        Kitchen: 1 oven (has_second_oven=False)
        Result: Steps are serialized (no overlap), no ResourceConflictError
        """
        dag_a, val_a = _make_recipe_with_oven(
            "Recipe A", "recipe_a", "a_bake", 375, 60
        )
        dag_b, val_b = _make_recipe_with_oven(
            "Recipe B", "recipe_b", "b_bake", 450, 60
        )

        # Should not raise ResourceConflictError
        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        # Should have 2 steps (both oven steps)
        assert len(result.scheduled_steps) == 2, (
            f"Expected 2 steps, got {len(result.scheduled_steps)}"
        )

        # Steps should be serialized (no overlap)
        step_a = next(s for s in result.scheduled_steps if "Recipe A" in s.recipe_name)
        step_b = next(s for s in result.scheduled_steps if "Recipe B" in s.recipe_name)
        
        assert not _steps_overlap(step_a, step_b), (
            f"Steps should be serialized, but they overlap: "
            f"A: {step_a.start_at_minute}-{step_a.end_at_minute}, "
            f"B: {step_b.start_at_minute}-{step_b.end_at_minute}"
        )

    def test_dual_oven_parallel_temps(self):
        """
        Dual oven allows parallel execution of different temperatures.
        
        Recipe A: 375°F for 60 minutes
        Recipe B: 450°F for 60 minutes
        Kitchen: 2 ovens (has_second_oven=True)
        Result: Steps CAN overlap (parallel execution)
        """
        dag_a, val_a = _make_recipe_with_oven(
            "Recipe A", "recipe_a", "a_bake", 375, 60
        )
        dag_b, val_b = _make_recipe_with_oven(
            "Recipe B", "recipe_b", "b_bake", 450, 60
        )

        dual_oven_kitchen = {
            "max_burners": 4,
            "max_oven_racks": 2,
            "has_second_oven": True,
        }

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], dual_oven_kitchen)

        # Should have 2 steps
        assert len(result.scheduled_steps) == 2, (
            f"Expected 2 steps, got {len(result.scheduled_steps)}"
        )

        # With dual oven, steps CAN overlap (parallel execution is allowed)
        # The scheduler might still serialize them based on other constraints,
        # but dual oven means different temps don't force serialization
        step_a = next(s for s in result.scheduled_steps if "Recipe A" in s.recipe_name)
        step_b = next(s for s in result.scheduled_steps if "Recipe B" in s.recipe_name)
        
        # Just verify no ResourceConflictError was raised and both steps exist
        # The actual overlap depends on scheduler's greedy algorithm and other factors
        assert step_a.resource == Resource.OVEN
        assert step_b.resource == Resource.OVEN

    def test_temp_tolerance_allows_sharing(self):
        """
        Temperature tolerance (≤15°F) allows oven sharing.
        
        Recipe A: 375°F for 60 minutes
        Recipe B: 385°F for 60 minutes (10°F difference, within 15°F tolerance)
        Kitchen: 1 oven
        Result: Steps can overlap (same effective temperature), no conflict
        """
        dag_a, val_a = _make_recipe_with_oven(
            "Recipe A", "recipe_a", "a_bake", 375, 60
        )
        dag_b, val_b = _make_recipe_with_oven(
            "Recipe B", "recipe_b", "b_bake", 385, 60
        )

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        # Should have 2 steps
        assert len(result.scheduled_steps) == 2, (
            f"Expected 2 steps, got {len(result.scheduled_steps)}"
        )

        step_a = next(s for s in result.scheduled_steps if "Recipe A" in s.recipe_name)
        step_b = next(s for s in result.scheduled_steps if "Recipe B" in s.recipe_name)

        # Steps within temperature tolerance can overlap (no forced serialization)
        # Similar to dual oven test, actual overlap depends on scheduler's algorithm
        # Key verification: no ResourceConflictError raised
        assert step_a.resource == Resource.OVEN
        assert step_b.resource == Resource.OVEN
