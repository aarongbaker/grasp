"""
tests/test_phase6_unit.py
Unit tests for Phase 6: DAG Builder + DAG Merger.

These tests call the algorithm functions directly — no graph, no database,
no mocks. They verify:
  - DAG builder: edge extraction, cycle detection, slug generation
  - DAG merger: critical path computation, greedy scheduling, resource
    contention, exact fixture match
"""

import pytest
from datetime import datetime

from models.recipe import (
    Ingredient, RawRecipe, RecipeStep, EnrichedRecipe, ValidatedRecipe,
)
from models.scheduling import RecipeDAG, MergedDAG, ScheduledStep
from models.enums import Resource

from graph.nodes.dag_builder import _build_single_dag, _generate_recipe_slug
from graph.nodes.dag_merger import (
    _merge_dags, _compute_critical_paths, _StepInfo, ResourceConflictError,
)

from tests.fixtures.recipes import (
    ENRICHED_SHORT_RIBS,
    ENRICHED_POMMES_PUREE,
    ENRICHED_CHOCOLATE_FONDANT,
    CYCLIC_STEPS_SHORT_RIBS,
)
from tests.fixtures.schedules import (
    RECIPE_DAG_SHORT_RIBS,
    RECIPE_DAG_POMMES_PUREE,
    RECIPE_DAG_FONDANT,
    MERGED_DAG_FULL,
    MERGED_DAG_TWO_RECIPE,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_validated(enriched: EnrichedRecipe) -> ValidatedRecipe:
    """Wrap an EnrichedRecipe in a ValidatedRecipe for testing."""
    return ValidatedRecipe(source=enriched, validated_at=datetime.now())


VALIDATED_SHORT_RIBS = _make_validated(ENRICHED_SHORT_RIBS)
VALIDATED_POMMES_PUREE = _make_validated(ENRICHED_POMMES_PUREE)
VALIDATED_FONDANT = _make_validated(ENRICHED_CHOCOLATE_FONDANT)

DEFAULT_KITCHEN = {
    "max_burners": 4,
    "max_oven_racks": 2,
    "has_second_oven": False,
}


# ── DAG Builder Tests ────────────────────────────────────────────────────────

class TestBuildSingleDag:
    def test_happy_path_short_ribs(self):
        """Valid recipe produces correct RecipeDAG with expected edges."""
        dag = _build_single_dag(VALIDATED_SHORT_RIBS)

        assert dag.recipe_name == "Braised Short Ribs"
        assert dag.recipe_slug == "braised_short_ribs"
        assert dag.steps == []  # Steps live in EnrichedRecipe
        assert len(dag.edges) == 3
        assert ("short_rib_step_1", "short_rib_step_2") in dag.edges
        assert ("short_rib_step_2", "short_rib_step_3") in dag.edges
        assert ("short_rib_step_3", "short_rib_step_4") in dag.edges

    def test_happy_path_fondant(self):
        """Fondant has 4 edges (5 steps, linear chain)."""
        dag = _build_single_dag(VALIDATED_FONDANT)

        assert dag.recipe_slug == "chocolate_fondant"  # from "Chocolate Fondant"
        assert len(dag.edges) == 4

    def test_detects_cycle(self):
        """Circular depends_on raises ValueError with cycle info."""
        cyclic_enriched = EnrichedRecipe(
            source=RawRecipe(
                name="Cyclic Recipe",
                description="test",
                servings=2,
                cuisine="test",
                estimated_total_minutes=30,
                ingredients=[Ingredient(name="x", quantity="1")],
                steps=["step one", "step two"],
            ),
            steps=CYCLIC_STEPS_SHORT_RIBS,
        )
        validated = ValidatedRecipe(source=cyclic_enriched, validated_at=datetime.now())

        with pytest.raises(ValueError, match="Cycle"):
            _build_single_dag(validated)


class TestSlugGeneration:
    def test_braised_short_ribs(self):
        assert _generate_recipe_slug("Braised Short Ribs") == "braised_short_ribs"

    def test_chocolate_fondant(self):
        assert _generate_recipe_slug("Chocolate Fondant") == "chocolate_fondant"

    def test_pommes_puree(self):
        assert _generate_recipe_slug("Pommes Puree") == "pommes_puree"

    def test_special_characters(self):
        assert _generate_recipe_slug("Crème Brûlée (French)") == "cr_me_br_l_e_french"


# ── DAG Merger Tests ─────────────────────────────────────────────────────────

class TestCriticalPath:
    def test_critical_path_values(self):
        """Verify critical path lengths for all steps across 3 recipes."""
        steps = []
        all_edges = []

        for dag, validated in [
            (RECIPE_DAG_SHORT_RIBS, VALIDATED_SHORT_RIBS),
            (RECIPE_DAG_POMMES_PUREE, VALIDATED_POMMES_PUREE),
            (RECIPE_DAG_FONDANT, VALIDATED_FONDANT),
        ]:
            for step in validated.source.steps:
                steps.append(_StepInfo(
                    step_id=step.step_id,
                    recipe_name=dag.recipe_name,
                    recipe_slug=dag.recipe_slug,
                    description=step.description,
                    resource=step.resource,
                    duration_minutes=step.duration_minutes,
                    depends_on=list(step.depends_on),
                ))
            all_edges.extend(dag.edges)

        cp = _compute_critical_paths(steps, all_edges)

        # Short Ribs: 20+10+150+15 = 195
        assert cp["short_rib_step_1"] == 195
        assert cp["short_rib_step_2"] == 175
        assert cp["short_rib_step_3"] == 165
        assert cp["short_rib_step_4"] == 15

        # Fondant: 10+15+10+30+12 = 77
        assert cp["fondant_step_1"] == 77
        assert cp["fondant_step_5"] == 12

        # Pommes: 30+10+15 = 55
        assert cp["pommes_puree_step_1"] == 55
        assert cp["pommes_puree_step_3"] == 15


class TestMergeDags:
    def test_three_recipe_exact_match(self):
        """Full 3-recipe merge must exactly match the fixture schedule."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        assert result.total_duration_minutes == MERGED_DAG_FULL.total_duration_minutes

        # Check each step's timing matches the fixture
        result_steps = {s.step_id: s for s in result.scheduled_steps}
        fixture_steps = {s.step_id: s for s in MERGED_DAG_FULL.scheduled_steps}

        assert set(result_steps.keys()) == set(fixture_steps.keys()), (
            f"Step ID mismatch: {set(result_steps.keys()) ^ set(fixture_steps.keys())}"
        )

        for step_id in fixture_steps:
            r = result_steps[step_id]
            f = fixture_steps[step_id]
            assert r.start_at_minute == f.start_at_minute, (
                f"{step_id}: start {r.start_at_minute} != fixture {f.start_at_minute}"
            )
            assert r.end_at_minute == f.end_at_minute, (
                f"{step_id}: end {r.end_at_minute} != fixture {f.end_at_minute}"
            )

    def test_three_recipe_sort_order(self):
        """Output steps must be sorted by (start_at_minute, recipe_slug, step_id)."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        # Verify order matches fixture order exactly
        result_ids = [s.step_id for s in result.scheduled_steps]
        fixture_ids = [s.step_id for s in MERGED_DAG_FULL.scheduled_steps]
        assert result_ids == fixture_ids, (
            f"Sort order mismatch:\n  result:  {result_ids}\n  fixture: {fixture_ids}"
        )

    def test_two_recipe_exact_match(self):
        """2-recipe merge (no fondant) must match the 2-recipe fixture."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        assert result.total_duration_minutes == MERGED_DAG_TWO_RECIPE.total_duration_minutes

        result_steps = {s.step_id: s for s in result.scheduled_steps}
        fixture_steps = {s.step_id: s for s in MERGED_DAG_TWO_RECIPE.scheduled_steps}

        for step_id in fixture_steps:
            r = result_steps[step_id]
            f = fixture_steps[step_id]
            assert r.start_at_minute == f.start_at_minute, (
                f"{step_id}: start {r.start_at_minute} != fixture {f.start_at_minute}"
            )

    def test_single_recipe(self):
        """Single recipe produces a sequential schedule."""
        result = _merge_dags(
            [RECIPE_DAG_SHORT_RIBS],
            [VALIDATED_SHORT_RIBS],
            DEFAULT_KITCHEN,
        )

        assert result.total_duration_minutes == 195  # 20+10+150+15
        assert len(result.scheduled_steps) == 4


class TestResourceContention:
    def test_stovetop_multi_burner(self):
        """3 STOVETOP steps with capacity=4 all start at T+0."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        stovetop_at_zero = [
            s for s in result.scheduled_steps
            if s.start_at_minute == 0 and s.resource == Resource.STOVETOP
        ]
        assert len(stovetop_at_zero) == 3, (
            f"Expected 3 STOVETOP steps at T+0, got {len(stovetop_at_zero)}"
        )

    def test_oven_exclusion(self):
        """Two OVEN steps (braise + fondant bake) cannot overlap with 1 oven."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        oven_steps = [s for s in result.scheduled_steps if s.resource == Resource.OVEN]
        assert len(oven_steps) == 2

        # They should NOT overlap
        braise = next(s for s in oven_steps if "short_rib" in s.step_id)
        bake = next(s for s in oven_steps if "fondant" in s.step_id)
        assert braise.end_at_minute <= bake.start_at_minute or bake.end_at_minute <= braise.start_at_minute, (
            f"OVEN overlap: braise [{braise.start_at_minute},{braise.end_at_minute}) "
            f"vs bake [{bake.start_at_minute},{bake.end_at_minute})"
        )

    def test_oven_with_second_oven(self):
        """With has_second_oven=True, two OVEN steps CAN overlap."""
        # Build two minimal recipes, each with a single OVEN step
        raw_a = RawRecipe(
            name="Recipe A", description="t", servings=2, cuisine="t",
            estimated_total_minutes=60, ingredients=[], steps=["bake"],
        )
        raw_b = RawRecipe(
            name="Recipe B", description="t", servings=2, cuisine="t",
            estimated_total_minutes=60, ingredients=[], steps=["bake"],
        )
        enriched_a = EnrichedRecipe(source=raw_a, steps=[
            RecipeStep(step_id="a_step_1", description="bake A",
                       duration_minutes=60, resource=Resource.OVEN),
        ])
        enriched_b = EnrichedRecipe(source=raw_b, steps=[
            RecipeStep(step_id="b_step_1", description="bake B",
                       duration_minutes=60, resource=Resource.OVEN),
        ])

        dags = [
            RecipeDAG(recipe_name="Recipe A", recipe_slug="recipe_a", steps=[], edges=[]),
            RecipeDAG(recipe_name="Recipe B", recipe_slug="recipe_b", steps=[], edges=[]),
        ]
        validated = [
            ValidatedRecipe(source=enriched_a, validated_at=datetime.now()),
            ValidatedRecipe(source=enriched_b, validated_at=datetime.now()),
        ]

        # With 1 oven: second step delayed
        result_1 = _merge_dags(dags, validated, {"max_burners": 4, "has_second_oven": False})
        assert result_1.total_duration_minutes == 120  # sequential

        # With 2 ovens: both at T+0
        result_2 = _merge_dags(dags, validated, {"max_burners": 4, "has_second_oven": True})
        assert result_2.total_duration_minutes == 60  # parallel

    def test_hands_exclusive(self):
        """No two HANDS steps overlap in the schedule."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        hands_steps = sorted(
            [s for s in result.scheduled_steps if s.resource == Resource.HANDS],
            key=lambda s: s.start_at_minute,
        )

        for i in range(len(hands_steps) - 1):
            a = hands_steps[i]
            b = hands_steps[i + 1]
            assert a.end_at_minute <= b.start_at_minute, (
                f"HANDS overlap: {a.step_id} [{a.start_at_minute},{a.end_at_minute}) "
                f"vs {b.step_id} [{b.start_at_minute},{b.end_at_minute})"
            )

    def test_passive_parallelism(self):
        """PASSIVE steps overlap with any other resource type."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        # SR3 (OVEN, T+30-180) and CF4 (PASSIVE, T+55-85) overlap
        sr3 = next(s for s in result.scheduled_steps if s.step_id == "short_rib_step_3")
        cf4 = next(s for s in result.scheduled_steps if s.step_id == "fondant_step_4")
        assert sr3.start_at_minute < cf4.end_at_minute and cf4.start_at_minute < sr3.end_at_minute, (
            "SR3 (OVEN) and CF4 (PASSIVE) should overlap"
        )


class TestResourceUtilisation:
    def test_utilisation_populated(self):
        """resource_utilisation dict has correct keys and interval counts."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        assert "stovetop" in result.resource_utilisation
        assert "hands" in result.resource_utilisation
        assert "oven" in result.resource_utilisation
        assert "passive" not in result.resource_utilisation  # PASSIVE not tracked

        assert len(result.resource_utilisation["stovetop"]) == 3  # 3 burner uses
        assert len(result.resource_utilisation["hands"]) == 5     # 5 HANDS steps
        assert len(result.resource_utilisation["oven"]) == 2      # braise + bake
