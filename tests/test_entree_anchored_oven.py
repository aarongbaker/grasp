"""
tests/test_entree_anchored_oven.py
Unit tests for the entree-anchored oven temperature strategy.

When an oven temperature conflict involves the entree recipe, the dag_merger
should classify it as resequence_required (not irreconcilable), allowing the
greedy scheduler to naturally serialize non-entree oven steps before or after
the entree's window.
"""

import pytest

from app.graph.nodes.dag_merger import (
    _OvenInterval,
    _build_one_oven_conflict_metadata,
    _involves_entree,
)
from app.models.recipe import EnrichedRecipe, Ingredient, RawRecipe, RecipeProvenance, RecipeStep
from app.models.enums import Resource


# ── Helper ────────────────────────────────────────────────────────────────────

def _oven_interval(start: int, end: int, temp_f: int, recipe_name: str, course: str | None) -> _OvenInterval:
    return _OvenInterval(
        start=start,
        end=end,
        temp_f=temp_f,
        recipe_name=recipe_name,
        step_id=f"{recipe_name}_oven_step",
        course=course,
    )


# ── _involves_entree ──────────────────────────────────────────────────────────

class TestInvolvesEntree:
    def test_entree_in_first_position(self):
        a = _oven_interval(0, 90, 320, "Boeuf Bourguignon", "entree")
        b = _oven_interval(0, 15, 500, "Soupe à l'Oignon", "soup")
        assert _involves_entree(a, b) is True

    def test_entree_in_second_position(self):
        a = _oven_interval(0, 15, 500, "Soupe à l'Oignon", "soup")
        b = _oven_interval(0, 90, 320, "Boeuf Bourguignon", "entree")
        assert _involves_entree(a, b) is True

    def test_no_entree_both_non_entree(self):
        a = _oven_interval(0, 15, 500, "Soupe à l'Oignon", "soup")
        b = _oven_interval(10, 22, 200, "Chocolate Fondant", "dessert")
        assert _involves_entree(a, b) is False

    def test_no_entree_both_none_course(self):
        a = _oven_interval(0, 90, 320, "Dish A", None)
        b = _oven_interval(0, 15, 500, "Dish B", None)
        assert _involves_entree(a, b) is False

    def test_entree_vs_none_course(self):
        a = _oven_interval(0, 90, 320, "Braise", "entree")
        b = _oven_interval(0, 15, 500, "Dish B", None)
        assert _involves_entree(a, b) is True


# ── _build_one_oven_conflict_metadata with entree anchor ─────────────────────

class TestEntreeAnchorConflictClassification:
    """Verify that overlapping entree + non-entree oven conflict becomes
    resequence_required (not irreconcilable) even in finish-together mode."""

    def test_entree_involved_overlap_is_resequence_not_irreconcilable(self):
        """Core regression test — reproduces the French dinner failure scenario."""
        entree_interval = _oven_interval(17, 107, 320, "Boeuf Bourguignon", "entree")
        soup_interval = _oven_interval(17, 29, 500, "Soupe à l'Oignon", "soup")

        result = _build_one_oven_conflict_metadata(
            [entree_interval, soup_interval],
            has_second_oven=False,
            treat_overlap_as_irreconcilable=True,
        )

        assert result.classification == "resequence_required", (
            f"Expected resequence_required but got {result.classification}. "
            "Entree-involved conflicts should be scheduleable, not fatal."
        )

    def test_entree_as_second_interval_also_resequences(self):
        """Order of intervals should not matter for entree detection."""
        soup_interval = _oven_interval(17, 29, 500, "Soupe à l'Oignon", "soup")
        entree_interval = _oven_interval(17, 107, 320, "Boeuf Bourguignon", "entree")

        result = _build_one_oven_conflict_metadata(
            [soup_interval, entree_interval],
            has_second_oven=False,
            treat_overlap_as_irreconcilable=True,
        )

        assert result.classification == "resequence_required"

    def test_two_non_entree_overlap_remains_irreconcilable(self):
        """Non-entree overlapping conflict in finish-together mode stays irreconcilable."""
        soup_interval = _oven_interval(17, 29, 500, "Soupe à l'Oignon", "soup")
        dessert_interval = _oven_interval(20, 32, 200, "Chocolate Fondant", "dessert")

        result = _build_one_oven_conflict_metadata(
            [soup_interval, dessert_interval],
            has_second_oven=False,
            treat_overlap_as_irreconcilable=True,
        )

        assert result.classification == "irreconcilable"

    def test_two_non_entree_overlap_with_none_course_remains_irreconcilable(self):
        """Intervals with no course set (None) are not treated as entree."""
        a = _oven_interval(0, 90, 320, "Dish A", None)
        b = _oven_interval(0, 15, 500, "Dish B", None)

        result = _build_one_oven_conflict_metadata(
            [a, b],
            has_second_oven=False,
            treat_overlap_as_irreconcilable=True,
        )

        assert result.classification == "irreconcilable"

    def test_entree_overlap_without_serving_time_still_resequences(self):
        """In ASAP mode (treat_overlap_as_irreconcilable=False), entree conflicts
        were already resequence_required — this should still hold."""
        entree_interval = _oven_interval(17, 107, 320, "Boeuf Bourguignon", "entree")
        soup_interval = _oven_interval(17, 29, 500, "Soupe à l'Oignon", "soup")

        result = _build_one_oven_conflict_metadata(
            [entree_interval, soup_interval],
            has_second_oven=False,
            treat_overlap_as_irreconcilable=False,
        )

        assert result.classification == "resequence_required"

    def test_compatible_temps_remain_compatible(self):
        """Intervals within 15°F tolerance are compatible regardless of course."""
        entree_interval = _oven_interval(0, 90, 320, "Braise", "entree")
        side_interval = _oven_interval(0, 30, 330, "Root Veg", "side")

        result = _build_one_oven_conflict_metadata(
            [entree_interval, side_interval],
            has_second_oven=False,
            treat_overlap_as_irreconcilable=True,
        )

        assert result.classification == "compatible"

    def test_second_oven_short_circuits_before_entree_logic(self):
        """has_second_oven=True means no conflict regardless of course or temp."""
        entree_interval = _oven_interval(0, 90, 320, "Braise", "entree")
        soup_interval = _oven_interval(0, 15, 500, "Soup Gratin", "soup")

        result = _build_one_oven_conflict_metadata(
            [entree_interval, soup_interval],
            has_second_oven=True,
            treat_overlap_as_irreconcilable=True,
        )

        assert result.classification == "compatible"


# ── RawRecipe course field ────────────────────────────────────────────────────

class TestRawRecipeCourseField:
    def test_course_field_is_optional_defaults_to_none(self):
        recipe = RawRecipe(
            name="Test Recipe",
            description="Test",
            servings=4,
            cuisine="Test",
            estimated_total_minutes=30,
            ingredients=[Ingredient(name="ingredient", quantity="1")],
            steps=["Do something"],
        )
        assert recipe.course is None

    def test_course_field_accepts_valid_literals(self):
        for course in ("appetizer", "soup", "salad", "entree", "side", "dessert", "other"):
            recipe = RawRecipe(
                name="Test Recipe",
                description="Test",
                servings=4,
                cuisine="Test",
                estimated_total_minutes=30,
                ingredients=[Ingredient(name="ingredient", quantity="1")],
                steps=["Do something"],
                course=course,
            )
            assert recipe.course == course

    def test_course_propagates_through_enriched_recipe_composition(self):
        raw = RawRecipe(
            name="Boeuf Bourguignon",
            description="Slow braise",
            servings=4,
            cuisine="French",
            estimated_total_minutes=210,
            ingredients=[Ingredient(name="beef", quantity="1kg")],
            steps=["Brown beef", "Braise"],
            course="entree",
        )
        enriched = EnrichedRecipe(
            source=raw,
            steps=[
                RecipeStep(
                    step_id="braise_step_1",
                    description="Brown beef",
                    duration_minutes=20,
                    resource=Resource.STOVETOP,
                ),
                RecipeStep(
                    step_id="braise_step_2",
                    description="Braise in oven",
                    duration_minutes=150,
                    depends_on=["braise_step_1"],
                    resource=Resource.OVEN,
                    oven_temp_f=320,
                ),
            ],
        )
        assert enriched.source.course == "entree"
