"""
tests/test_phase6_unit.py
Unit tests for Phase 6: DAG Builder + DAG Merger.

These tests call the algorithm functions directly — no graph, no database,
no mocks. They verify:
  - DAG builder: edge extraction, cycle detection, slug generation
  - DAG merger: critical path computation, greedy scheduling, resource
    contention, exact fixture match
"""

from datetime import datetime

import pytest

from app.graph.nodes.dag_builder import _build_single_dag, _generate_recipe_slug
from app.graph.nodes.dag_merger import (
    ResourceConflictError,
    _compute_critical_paths,
    _IntervalIndex,
    _merge_dags,
    _StepInfo,
)
from app.models.enums import Resource
from app.models.recipe import (
    EnrichedRecipe,
    Ingredient,
    RawRecipe,
    RecipeStep,
    ValidatedRecipe,
)
from app.models.scheduling import MergedDAG, RecipeDAG, ScheduledStep
from tests.fixtures.recipes import (
    CYCLIC_STEPS_SHORT_RIBS,
    ENRICHED_CHOCOLATE_FONDANT,
    ENRICHED_POMMES_PUREE,
    ENRICHED_SHORT_RIBS,
)
from tests.fixtures.schedules import (
    MERGED_DAG_FULL,
    MERGED_DAG_TWO_RECIPE,
    RECIPE_DAG_FONDANT,
    RECIPE_DAG_POMMES_PUREE,
    RECIPE_DAG_SHORT_RIBS,
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
                steps.append(
                    _StepInfo(
                        step_id=step.step_id,
                        recipe_name=dag.recipe_name,
                        recipe_slug=dag.recipe_slug,
                        description=step.description,
                        resource=step.resource,
                        duration_minutes=step.duration_minutes,
                        depends_on=list(step.depends_on),
                    )
                )
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
            assert r.end_at_minute == f.end_at_minute, f"{step_id}: end {r.end_at_minute} != fixture {f.end_at_minute}"

    def test_three_recipe_sort_order(self):
        """Output steps must be sorted by (start_at_minute, recipe_slug, step_id)."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        # Verify order matches fixture order exactly
        result_ids = [s.step_id for s in result.scheduled_steps]
        fixture_ids = [s.step_id for s in MERGED_DAG_FULL.scheduled_steps]
        assert result_ids == fixture_ids, f"Sort order mismatch:\n  result:  {result_ids}\n  fixture: {fixture_ids}"

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
            s for s in result.scheduled_steps if s.start_at_minute == 0 and s.resource == Resource.STOVETOP
        ]
        assert len(stovetop_at_zero) == 3, f"Expected 3 STOVETOP steps at T+0, got {len(stovetop_at_zero)}"

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
            name="Recipe A",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=60,
            ingredients=[],
            steps=["bake"],
        )
        raw_b = RawRecipe(
            name="Recipe B",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=60,
            ingredients=[],
            steps=["bake"],
        )
        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(step_id="a_step_1", description="bake A", duration_minutes=60, resource=Resource.OVEN),
            ],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(step_id="b_step_1", description="bake B", duration_minutes=60, resource=Resource.OVEN),
            ],
        )

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
        assert len(result.resource_utilisation["hands"]) == 5  # 5 HANDS steps
        assert len(result.resource_utilisation["oven"]) == 2  # braise + bake


class TestWorstCase:
    def test_total_duration_max(self):
        """Worst-case total accounts for duration_max on SR3 (braise 150-180 min)."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)
        # SR3 worst-case: start=30, dur_max=180, end_max=210
        # SR4 depends on SR3, starts at 180 (optimistic). If SR3 takes max → SR4 at 210, ends 225
        # But the merger places SR4 at optimistic timing. Worst case total = max(end_max)
        # SR3 end_max = 30+180 = 210, SR4 end_max = 180+15 = 195
        # So total_max = 210 (from SR3)
        # Wait — CF5 end_max = 180 + 14 = 194. SR4 has no duration_max so end_max = 195.
        # SR3 end_max = 210, which is the max overall
        assert result.total_duration_minutes_max == 210

    def test_end_at_minute_max(self):
        """Steps with duration_max get correct end_at_minute_max."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)
        by_id = {s.step_id: s for s in result.scheduled_steps}

        # SR3: start=30, dur_max=180, end_max=210
        sr3 = by_id["short_rib_step_3"]
        assert sr3.end_at_minute_max == 210

        # CF5: start=180, dur_max=14, end_max=194
        cf5 = by_id["fondant_step_5"]
        assert cf5.end_at_minute_max == 194

        # SR1: no dur_max → end_max = end_at_minute = 20
        sr1 = by_id["short_rib_step_1"]
        assert sr1.end_at_minute_max == 20

    def test_slack_minutes(self):
        """SR3 (braise) has negative slack: overrunning delays SR4."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)
        by_id = {s.step_id: s for s in result.scheduled_steps}

        # SR3 end_max=210, successor SR4 starts at 180 → slack = 180 - 210 = -30 → clamped to 0
        assert by_id["short_rib_step_3"].slack_minutes == 0

        # SR1 end_max=20, successor SR2 starts at 20 → slack = 20 - 20 = 0
        assert by_id["short_rib_step_1"].slack_minutes == 0

    def test_no_max_when_equal(self):
        """total_duration_minutes_max is None when no steps have duration_max."""
        # Single recipe with no duration_max steps
        raw = RawRecipe(
            name="Simple",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=10,
            ingredients=[],
            steps=["cook"],
        )
        enriched = EnrichedRecipe(
            source=raw,
            steps=[RecipeStep(step_id="s1", description="cook", duration_minutes=10, resource=Resource.HANDS)],
        )
        dag = RecipeDAG(recipe_name="Simple", recipe_slug="simple", steps=[], edges=[])
        validated = [ValidatedRecipe(source=enriched, validated_at=datetime.now())]
        result = _merge_dags([dag], validated, DEFAULT_KITCHEN)
        assert result.total_duration_minutes_max is None


class TestActiveTime:
    def test_three_recipe_active_time(self):
        """Active time excludes PASSIVE steps (SR4=15, CF4=30)."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)
        assert result.active_time_minutes == 282  # total 327 - PASSIVE 45

    def test_two_recipe_active_time(self):
        """2-recipe active time excludes SR4 (PASSIVE, 15 min)."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)
        assert result.active_time_minutes == 235  # total 250 - PASSIVE 15


class TestIntervalIndex:
    def test_empty_index(self):
        idx = _IntervalIndex()
        assert idx.count_overlapping(0, 10) == 0
        assert idx.min_end_after(0) is None
        assert len(idx) == 0

    def test_single_interval(self):
        idx = _IntervalIndex()
        idx.add(5, 15)
        assert idx.count_overlapping(0, 10) == 1
        assert idx.count_overlapping(10, 20) == 1
        assert idx.count_overlapping(15, 25) == 0  # [15,25) doesn't overlap [5,15)
        assert idx.count_overlapping(0, 5) == 0  # [0,5) doesn't overlap [5,15)

    def test_multiple_intervals(self):
        idx = _IntervalIndex()
        idx.add(0, 10)
        idx.add(5, 20)
        idx.add(15, 30)
        assert idx.count_overlapping(0, 10) == 2  # [0,10) and [5,20)
        assert idx.count_overlapping(10, 15) == 1  # only [5,20)
        assert idx.count_overlapping(0, 30) == 3

    def test_min_end_after(self):
        idx = _IntervalIndex()
        idx.add(0, 10)
        idx.add(5, 20)
        idx.add(15, 30)
        assert idx.min_end_after(0) == 10
        assert idx.min_end_after(10) == 20
        assert idx.min_end_after(25) == 30
        assert idx.min_end_after(30) is None

    def test_intervals_sorted(self):
        idx = _IntervalIndex()
        idx.add(10, 20)
        idx.add(0, 5)
        idx.add(5, 15)
        assert idx.intervals() == [(0, 5), (5, 15), (10, 20)]


class TestEquipmentContention:
    """Equipment-aware scheduling: named equipment pieces have capacity=1."""

    def _make_recipe(self, name, slug, step_id, resource, duration, equipment=None):
        """Helper to build a minimal recipe with one step."""
        raw = RawRecipe(
            name=name,
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=duration,
            ingredients=[],
            steps=["do"],
        )
        enriched = EnrichedRecipe(
            source=raw,
            steps=[
                RecipeStep(
                    step_id=step_id,
                    description=f"use {name}",
                    duration_minutes=duration,
                    resource=resource,
                    required_equipment=equipment or [],
                ),
            ],
        )
        dag = RecipeDAG(recipe_name=name, recipe_slug=slug, steps=[], edges=[])
        validated = ValidatedRecipe(source=enriched, validated_at=datetime.now())
        return dag, validated

    def test_equipment_serialises_steps(self):
        """Two STOVETOP steps needing the same equipment cannot overlap."""
        dag_a, val_a = self._make_recipe("A", "a", "a_step_1", Resource.STOVETOP, 30, ["stand_mixer"])
        dag_b, val_b = self._make_recipe("B", "b", "b_step_1", Resource.STOVETOP, 20, ["stand_mixer"])
        kitchen = {"max_burners": 4, "equipment": ["stand_mixer"]}

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], kitchen)

        steps = sorted(result.scheduled_steps, key=lambda s: s.start_at_minute)
        # Both are STOVETOP (cap=4), so without equipment they'd overlap.
        # With stand_mixer (cap=1), they must be sequential.
        assert steps[0].end_at_minute <= steps[1].start_at_minute, (
            f"Equipment overlap: {steps[0].step_id} [{steps[0].start_at_minute},{steps[0].end_at_minute}) "
            f"vs {steps[1].step_id} [{steps[1].start_at_minute},{steps[1].end_at_minute})"
        )

    def test_different_equipment_allows_overlap(self):
        """Two STOVETOP steps with different equipment CAN overlap."""
        dag_a, val_a = self._make_recipe("A", "a", "a_step_1", Resource.STOVETOP, 30, ["stand_mixer"])
        dag_b, val_b = self._make_recipe("B", "b", "b_step_1", Resource.STOVETOP, 20, ["food_processor"])
        kitchen = {"max_burners": 4, "equipment": ["stand_mixer", "food_processor"]}

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], kitchen)

        steps = sorted(result.scheduled_steps, key=lambda s: s.start_at_minute)
        # Different equipment — both should start at T+0
        assert steps[0].start_at_minute == 0
        assert steps[1].start_at_minute == 0

    def test_unknown_equipment_unconstrained(self):
        """Equipment not in kitchen_config is treated as unconstrained."""
        dag_a, val_a = self._make_recipe("A", "a", "a_step_1", Resource.STOVETOP, 30, ["sous_vide"])
        dag_b, val_b = self._make_recipe("B", "b", "b_step_1", Resource.STOVETOP, 20, ["sous_vide"])
        # No equipment in kitchen config — sous_vide not tracked
        kitchen = {"max_burners": 4}

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], kitchen)

        steps = sorted(result.scheduled_steps, key=lambda s: s.start_at_minute)
        # Both should start at T+0 (unconstrained)
        assert steps[0].start_at_minute == 0
        assert steps[1].start_at_minute == 0

    def test_equipment_utilisation_populated(self):
        """equipment_utilisation records intervals for used equipment."""
        dag_a, val_a = self._make_recipe("A", "a", "a_step_1", Resource.STOVETOP, 30, ["stand_mixer"])
        kitchen = {"max_burners": 4, "equipment": ["stand_mixer"]}

        result = _merge_dags([dag_a], [val_a], kitchen)

        assert "stand_mixer" in result.equipment_utilisation
        assert result.equipment_utilisation["stand_mixer"] == [(0, 30)]

    def test_no_equipment_no_utilisation(self):
        """Steps without equipment produce empty equipment_utilisation."""
        dag_a, val_a = self._make_recipe("A", "a", "a_step_1", Resource.STOVETOP, 30)
        kitchen = {"max_burners": 4, "equipment": ["stand_mixer"]}

        result = _merge_dags([dag_a], [val_a], kitchen)

        assert result.equipment_utilisation == {}

    def test_existing_fixtures_unaffected(self):
        """Existing 3-recipe fixtures still produce the same result (no equipment)."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        assert result.equipment_utilisation == {}
        assert result.total_duration_minutes == MERGED_DAG_FULL.total_duration_minutes
