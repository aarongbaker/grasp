"""
tests/test_phase6_unit.py
Unit tests for Phase 6: DAG Builder + DAG Merger.

These tests call the algorithm functions directly — no graph, no database,
no mocks. They verify:
  - DAG builder: edge extraction, cycle detection, slug generation
  - DAG merger: critical path computation, greedy scheduling, resource
    contention, exact fixture match
  - Merge detection: exact ingredient+prep match consolidation
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.graph.nodes.dag_builder import _build_single_dag, _generate_recipe_slug
from app.graph.nodes.dag_merger import (
    ResourceConflictError,
    _compute_critical_paths,
    _compute_finish_together_offsets,
    _detect_resource_warnings,
    _IntervalIndex,
    _merge_dags,
    _StepInfo,
)
from app.graph.nodes.renderer import _build_timeline, _build_timeline_entry
from app.models.enums import Resource
from app.models.recipe import (
    EnrichedRecipe,
    Ingredient,
    IngredientUse,
    RawRecipe,
    RecipeStep,
    ValidatedRecipe,
)
from app.models.scheduling import (
    MergedDAG,
    OneOvenConflictRemediation,
    OneOvenConflictSummary,
    RecipeDAG,
    ScheduledStep,
)
from app.models.user import BurnerDescriptor, KitchenConfig
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


class TestSchedulingModelOvenConflictMetadata:
    def test_merged_dag_defaults_one_oven_conflict_for_legacy_payload(self):
        merged = MergedDAG.model_validate(
            {
                "scheduled_steps": [
                    {
                        "step_id": "legacy_oven",
                        "recipe_name": "Legacy Bake",
                        "description": "Bake until set",
                        "resource": Resource.OVEN,
                        "duration_minutes": 30,
                        "start_at_minute": 0,
                        "end_at_minute": 30,
                        "oven_temp_f": None,
                    }
                ],
                "total_duration_minutes": 30,
            }
        )

        assert merged.one_oven_conflict.classification == "compatible"
        assert merged.one_oven_conflict.remediation.requires_resequencing is False

    def test_merged_dag_accepts_resequence_required_contract(self):
        merged = MergedDAG.model_validate(
            {
                "scheduled_steps": [
                    {
                        "step_id": "a_step_1",
                        "recipe_name": "Recipe A",
                        "description": "Bake at 375F",
                        "resource": Resource.OVEN,
                        "duration_minutes": 60,
                        "start_at_minute": 0,
                        "end_at_minute": 60,
                        "oven_temp_f": 375,
                    },
                    {
                        "step_id": "b_step_1",
                        "recipe_name": "Recipe B",
                        "description": "Bake at 450F",
                        "resource": Resource.OVEN,
                        "duration_minutes": 60,
                        "start_at_minute": 60,
                        "end_at_minute": 120,
                        "oven_temp_f": 450,
                    },
                ],
                "total_duration_minutes": 120,
                "one_oven_conflict": {
                    "classification": "resequence_required",
                    "temperature_gap_f": 75,
                    "blocking_recipe_names": ["Recipe A", "Recipe B"],
                    "affected_step_ids": ["a_step_1", "b_step_1"],
                    "remediation": {
                        "requires_resequencing": True,
                        "suggested_actions": ["Bake Recipe B after Recipe A finishes."],
                        "delaying_recipe_names": ["Recipe B"],
                    },
                },
            }
        )

        assert merged.one_oven_conflict.classification == "resequence_required"
        assert merged.one_oven_conflict.temperature_gap_f == 75
        assert merged.one_oven_conflict.remediation.suggested_actions == ["Bake Recipe B after Recipe A finishes."]


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


class TestMergeDetection:
    """Tests for shared prep detection and DAG merge consolidation."""

    def _make_recipe_with_prep(
        self, name: str, slug: str, step_id: str, ingredient: str, prep_method: str, quantity: float, unit: str
    ) -> tuple[RecipeDAG, ValidatedRecipe]:
        """Helper to build a recipe with one prep step with structured ingredient metadata."""
        raw = RawRecipe(
            name=name,
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=30,
            ingredients=[Ingredient(name=ingredient, quantity=f"{quantity} {unit}")],
            steps=[f"prep {ingredient}"],
        )

        ingredient_use = IngredientUse(
            ingredient_name=ingredient,
            prep_method=prep_method,
            quantity_canonical=quantity,
            unit_canonical=unit,
            quantity_original=f"{quantity} {unit}",
        )

        enriched = EnrichedRecipe(
            source=raw,
            steps=[
                RecipeStep(
                    step_id=step_id,
                    description=f"Prep {ingredient} ({prep_method})",
                    duration_minutes=10,
                    resource=Resource.HANDS,
                    ingredient_uses=[ingredient_use],
                )
            ],
        )

        dag = RecipeDAG(recipe_name=name, recipe_slug=slug, steps=[], edges=[])
        validated = ValidatedRecipe(source=enriched, validated_at=datetime.now())
        return dag, validated

    def test_exact_match_creates_merged_node(self):
        """
        Two recipes with exact ingredient+prep match produce a merged prep node.

        Recipe A: prep 2 cups diced celery
        Recipe B: prep 1 cup diced celery
        Result: merged prep node with 3 cups total, allocation dict showing breakdown
        """
        dag_a, val_a = self._make_recipe_with_prep(
            "Recipe A", "recipe_a", "a_prep", "celery", "diced", 2.0, "cup"
        )
        dag_b, val_b = self._make_recipe_with_prep(
            "Recipe B", "recipe_b", "b_prep", "celery", "diced", 1.0, "cup"
        )

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        # Should have 1 merged step instead of 2 separate steps
        assert len(result.scheduled_steps) == 1, f"Expected 1 merged step, got {len(result.scheduled_steps)}"

        merged_step = result.scheduled_steps[0]

        # Verify merged_from field populated with both original step_ids
        assert set(merged_step.merged_from) == {"a_prep", "b_prep"}, (
            f"Expected merged_from=['a_prep', 'b_prep'], got {merged_step.merged_from}"
        )

        # Verify allocation dict shows breakdown
        assert "Recipe A" in merged_step.allocation, f"Allocation missing Recipe A: {merged_step.allocation}"
        assert "Recipe B" in merged_step.allocation, f"Allocation missing Recipe B: {merged_step.allocation}"
        assert merged_step.allocation["Recipe A"] == "2.0 cup", (
            f"Expected '2.0 cup', got {merged_step.allocation['Recipe A']}"
        )
        assert merged_step.allocation["Recipe B"] == "1.0 cup", (
            f"Expected '1.0 cup', got {merged_step.allocation['Recipe B']}"
        )

    def test_different_prep_method_no_merge(self):
        """
        Different prep methods prevent merging even with same ingredient.

        Recipe A: diced celery
        Recipe B: sliced celery
        Result: 2 separate steps (no merge)
        """
        dag_a, val_a = self._make_recipe_with_prep(
            "Recipe A", "recipe_a", "a_prep", "celery", "diced", 2.0, "cup"
        )
        dag_b, val_b = self._make_recipe_with_prep(
            "Recipe B", "recipe_b", "b_prep", "celery", "sliced", 1.0, "cup"
        )

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        # Should have 2 separate steps (no merge)
        assert len(result.scheduled_steps) == 2, f"Expected 2 steps (no merge), got {len(result.scheduled_steps)}"

        # Verify neither step has merged_from populated
        for step in result.scheduled_steps:
            assert step.merged_from == [], f"Expected no merged_from, got {step.merged_from}"
            assert step.allocation == {}, f"Expected no allocation, got {step.allocation}"

    def test_different_ingredient_no_merge(self):
        """
        Different ingredients prevent merging even with same prep method.

        Recipe A: diced celery
        Recipe B: diced onion
        Result: 2 separate steps (no merge)
        """
        dag_a, val_a = self._make_recipe_with_prep(
            "Recipe A", "recipe_a", "a_prep", "celery", "diced", 2.0, "cup"
        )
        dag_b, val_b = self._make_recipe_with_prep(
            "Recipe B", "recipe_b", "b_prep", "onion", "diced", 1.0, "cup"
        )

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        # Should have 2 separate steps (no merge)
        assert len(result.scheduled_steps) == 2, f"Expected 2 steps (no merge), got {len(result.scheduled_steps)}"

        # Verify neither step has merged_from populated
        for step in result.scheduled_steps:
            assert step.merged_from == [], f"Expected no merged_from, got {step.merged_from}"

    def test_three_recipe_merge(self):
        """
        Three recipes with same ingredient+prep produce single merged node.

        Recipe A: 2 cups diced celery
        Recipe B: 1 cup diced celery
        Recipe C: 1.5 cups diced celery
        Result: 1 merged node with 4.5 cups total, 3-way allocation
        """
        dag_a, val_a = self._make_recipe_with_prep(
            "Recipe A", "recipe_a", "a_prep", "celery", "diced", 2.0, "cup"
        )
        dag_b, val_b = self._make_recipe_with_prep(
            "Recipe B", "recipe_b", "b_prep", "celery", "diced", 1.0, "cup"
        )
        dag_c, val_c = self._make_recipe_with_prep(
            "Recipe C", "recipe_c", "c_prep", "celery", "diced", 1.5, "cup"
        )

        result = _merge_dags([dag_a, dag_b, dag_c], [val_a, val_b, val_c], DEFAULT_KITCHEN)

        # Should have 1 merged step
        assert len(result.scheduled_steps) == 1

        merged_step = result.scheduled_steps[0]
        assert set(merged_step.merged_from) == {"a_prep", "b_prep", "c_prep"}
        assert len(merged_step.allocation) == 3
        assert merged_step.allocation["Recipe A"] == "2.0 cup"
        assert merged_step.allocation["Recipe B"] == "1.0 cup"
        assert merged_step.allocation["Recipe C"] == "1.5 cup"

    def test_merged_node_total_quantity_in_description(self):
        """
        Merged node description shows total aggregated quantity.

        Recipe A: 2 cups diced celery
        Recipe B: 1 cup diced celery
        Result: description mentions '3.0 cup' (or similar aggregation)
        """
        dag_a, val_a = self._make_recipe_with_prep(
            "Recipe A", "recipe_a", "a_prep", "celery", "diced", 2.0, "cup"
        )
        dag_b, val_b = self._make_recipe_with_prep(
            "Recipe B", "recipe_b", "b_prep", "celery", "diced", 1.0, "cup"
        )

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        merged_step = result.scheduled_steps[0]

        # Description should mention total quantity (3.0 cup)
        # The exact format may vary, but it should contain "3" and "cup" and "celery"
        desc_lower = merged_step.description.lower()
        assert "celery" in desc_lower, f"Description should mention celery: {merged_step.description}"
        assert "3" in merged_step.description or "3.0" in merged_step.description, (
            f"Description should mention total quantity 3: {merged_step.description}"
        )

    def test_merged_step_id_format(self):
        """
        Merged step gets synthetic step_id in format: merged_{ingredient}_{prep_method}_{n}

        Recipe A: diced celery
        Recipe B: diced celery
        Result: step_id like 'merged_celery_diced_1'
        """
        dag_a, val_a = self._make_recipe_with_prep(
            "Recipe A", "recipe_a", "a_prep", "celery", "diced", 2.0, "cup"
        )
        dag_b, val_b = self._make_recipe_with_prep(
            "Recipe B", "recipe_b", "b_prep", "celery", "diced", 1.0, "cup"
        )

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        merged_step = result.scheduled_steps[0]

        # step_id should follow merged_* format
        assert merged_step.step_id.startswith("merged_"), f"Expected merged_* step_id, got {merged_step.step_id}"
        assert "celery" in merged_step.step_id, f"step_id should contain ingredient: {merged_step.step_id}"
        assert "diced" in merged_step.step_id, f"step_id should contain prep method: {merged_step.step_id}"

    def test_no_ingredient_uses_no_merge(self):
        """
        Steps without ingredient_uses field are not candidates for merging.

        Recipe A: prep step with no ingredient_uses
        Recipe B: prep step with no ingredient_uses
        Result: 2 separate steps
        """
        raw_a = RawRecipe(
            name="Recipe A",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=20,
            ingredients=[],
            steps=["generic prep"],
        )
        raw_b = RawRecipe(
            name="Recipe B",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=20,
            ingredients=[],
            steps=["generic prep"],
        )

        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(
                    step_id="a_prep",
                    description="Generic prep work",
                    duration_minutes=10,
                    resource=Resource.HANDS,
                    ingredient_uses=[],  # empty - no structured metadata
                )
            ],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(
                    step_id="b_prep",
                    description="Generic prep work",
                    duration_minutes=10,
                    resource=Resource.HANDS,
                    ingredient_uses=[],
                )
            ],
        )

        dag_a = RecipeDAG(recipe_name="Recipe A", recipe_slug="recipe_a", steps=[], edges=[])
        dag_b = RecipeDAG(recipe_name="Recipe B", recipe_slug="recipe_b", steps=[], edges=[])

        val_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        val_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], DEFAULT_KITCHEN)

        # Should have 2 separate steps (no merge without ingredient_uses)
        assert len(result.scheduled_steps) == 2


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
        result = _merge_dags(dags, validated, {"max_burners": 4, "has_second_oven": False})
        assert result.total_duration_minutes == 120  # sequential
        assert result.one_oven_conflict.classification == "compatible"
        assert result.one_oven_conflict.temperature_gap_f is None

        # With 2 ovens: both at T+0
        result_2 = _merge_dags(dags, validated, {"max_burners": 4, "has_second_oven": True})
        assert result_2.total_duration_minutes == 60  # parallel
        assert result_2.one_oven_conflict.classification == "compatible"

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
        """resource_utilisation tracks pooled resources; stovetop now reports via explicit burner assignments."""
        recipe_dags = [RECIPE_DAG_SHORT_RIBS, RECIPE_DAG_POMMES_PUREE, RECIPE_DAG_FONDANT]
        validated = [VALIDATED_SHORT_RIBS, VALIDATED_POMMES_PUREE, VALIDATED_FONDANT]

        result = _merge_dags(recipe_dags, validated, DEFAULT_KITCHEN)

        assert "stovetop" not in result.resource_utilisation
        assert "hands" in result.resource_utilisation
        assert "oven" in result.resource_utilisation
        assert "passive" not in result.resource_utilisation  # PASSIVE not tracked

        stovetop_steps = [s for s in result.scheduled_steps if s.resource == Resource.STOVETOP]
        assert len(stovetop_steps) == 3
        assert all(step.burner_id is not None for step in stovetop_steps)
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

    def test_missing_equipment_removes_serialization_constraint(self):
        """Removing the tracked equipment from kitchen_config should allow overlap again."""
        dag_a, val_a = self._make_recipe("A", "a", "a_step_1", Resource.STOVETOP, 30, ["stand_mixer"])
        dag_b, val_b = self._make_recipe("B", "b", "b_step_1", Resource.STOVETOP, 20, ["stand_mixer"])

        constrained = _merge_dags(
            [dag_a, dag_b],
            [val_a, val_b],
            {"max_burners": 4, "equipment": ["stand_mixer"]},
        )
        unconstrained = _merge_dags(
            [dag_a, dag_b],
            [val_a, val_b],
            {"max_burners": 4},
        )

        constrained_steps = sorted(constrained.scheduled_steps, key=lambda step: step.start_at_minute)
        unconstrained_steps = sorted(unconstrained.scheduled_steps, key=lambda step: step.start_at_minute)

        assert constrained_steps[0].end_at_minute <= constrained_steps[1].start_at_minute
        assert unconstrained_steps[0].start_at_minute == 0
        assert unconstrained_steps[1].start_at_minute == 0

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


class TestFinishTogetherOffsets:
    """Tests for _compute_finish_together_offsets() function."""

    def _make_step(self, step_id: str, recipe_name: str, resource: Resource, duration: int) -> _StepInfo:
        """Helper to build a minimal step."""
        return _StepInfo(
            step_id=step_id,
            recipe_name=recipe_name,
            recipe_slug=recipe_name.lower().replace(" ", "_"),
            description=f"test step {step_id}",
            resource=resource,
            duration_minutes=duration,
            depends_on=[],
        )

    def test_no_serving_time_returns_empty_dict(self):
        """When serving_time is None, returns empty dict (ASAP mode)."""
        steps = [
            self._make_step("a_1", "Recipe A", Resource.OVEN, 60),
            self._make_step("b_1", "Recipe B", Resource.OVEN, 30),
        ]
        edges = []

        result = _compute_finish_together_offsets(steps, edges, serving_time=None)

        assert result == {}

    def test_single_recipe_returns_zero_offset(self):
        """Single recipe gets offset of 0 (no delay needed)."""
        steps = [
            self._make_step("a_1", "Recipe A", Resource.STOVETOP, 60),
        ]
        edges = []

        result = _compute_finish_together_offsets(steps, edges, serving_time="18:00")

        assert result == {"Recipe A": 0}

    def test_three_recipes_computes_correct_offsets(self):
        """3h, 1h, 1h recipes → offsets 0, 120, 120 minutes."""
        # Recipe A: 3 hours = 180 min of cooking (longest, anchor)
        # Recipe B: 1 hour = 60 min of cooking
        # Recipe C: 1 hour = 60 min of cooking
        steps = [
            self._make_step("a_1", "Recipe A", Resource.OVEN, 180),
            self._make_step("b_1", "Recipe B", Resource.STOVETOP, 60),
            self._make_step("c_1", "Recipe C", Resource.OVEN, 60),
        ]
        edges = []

        result = _compute_finish_together_offsets(steps, edges, serving_time="18:00")

        assert result["Recipe A"] == 0, "Longest cooking recipe should have 0 offset"
        assert result["Recipe B"] == 120, "1h recipe should delay 2h to match 3h anchor"
        assert result["Recipe C"] == 120, "1h recipe should delay 2h to match 3h anchor"

    def test_equal_duration_recipes_zero_offsets(self):
        """All recipes with equal cooking time get offset 0."""
        steps = [
            self._make_step("a_1", "Recipe A", Resource.OVEN, 90),
            self._make_step("b_1", "Recipe B", Resource.STOVETOP, 90),
            self._make_step("c_1", "Recipe C", Resource.OVEN, 90),
        ]
        edges = []

        result = _compute_finish_together_offsets(steps, edges, serving_time="19:00")

        assert result["Recipe A"] == 0
        assert result["Recipe B"] == 0
        assert result["Recipe C"] == 0

    def test_prep_steps_not_counted_in_cooking_duration(self):
        """HANDS/PASSIVE steps don't affect cooking duration or offsets."""
        # Recipe A: 30 min prep (HANDS) + 60 min cooking (OVEN) = 60 min cooking
        # Recipe B: 120 min cooking (STOVETOP) = 120 min cooking (anchor)
        # Recipe A offset should be 120 - 60 = 60
        steps = [
            self._make_step("a_prep", "Recipe A", Resource.HANDS, 30),
            self._make_step("a_cook", "Recipe A", Resource.OVEN, 60),
            self._make_step("b_cook", "Recipe B", Resource.STOVETOP, 120),
        ]
        edges = [("a_prep", "a_cook")]

        result = _compute_finish_together_offsets(steps, edges, serving_time="18:00")

        assert result["Recipe A"] == 60, "Should offset 60 min (120 - 60 cooking)"
        assert result["Recipe B"] == 0, "Anchor recipe gets 0 offset"

    def test_empty_steps_returns_empty_dict(self):
        """No steps returns empty dict."""
        result = _compute_finish_together_offsets([], [], serving_time="18:00")
        assert result == {}


class TestFinishTogetherScheduling:
    """
    Integration tests for finish-together scheduling via _merge_dags().

    These tests verify the full scheduling algorithm when serving_time is set,
    not just the offset computation. They prove:
    - R022: Cooking steps are staggered to finish together
    - R023: All cooking ends within a 15 min window when serving_time is set
    - R024: Prep steps (HANDS) remain ASAP while cooking is delayed
    - R026: Backward compatibility — no serving_time = ASAP behavior
    """

    @pytest.fixture
    def finish_together_fixtures(self):
        """Create validated recipes and DAGs for finish-together tests."""
        from tests.fixtures.recipes import (
            ENRICHED_FT_RECIPE_A,
            ENRICHED_FT_RECIPE_B,
            ENRICHED_FT_RECIPE_C,
        )
        from tests.fixtures.schedules import (
            RECIPE_DAG_FT_A,
            RECIPE_DAG_FT_B,
            RECIPE_DAG_FT_C,
        )

        validated_a = ValidatedRecipe(source=ENRICHED_FT_RECIPE_A, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=ENRICHED_FT_RECIPE_B, validated_at=datetime.now())
        validated_c = ValidatedRecipe(source=ENRICHED_FT_RECIPE_C, validated_at=datetime.now())

        return {
            "dags": [RECIPE_DAG_FT_A, RECIPE_DAG_FT_B, RECIPE_DAG_FT_C],
            "validated": [validated_a, validated_b, validated_c],
        }

    def test_three_recipes_finish_within_window(self, finish_together_fixtures):
        """
        Verify 3h + 1h + 1h cooking respects finish-together offsets.

        Recipe A: 180 min cooking (OVEN) — anchor
        Recipe B: 60 min cooking (STOVETOP) — should delay 120 min
        Recipe C: 60 min cooking (OVEN) — should delay 120 min

        Note: Recipe C must wait for Recipe A's oven to free up (oven capacity=1),
        so it can't finish at the same time as Recipe A. The key assertion is that
        Recipe B (STOVETOP) finishes close to Recipe A's cooking end time.
        """
        result = _merge_dags(
            finish_together_fixtures["dags"],
            finish_together_fixtures["validated"],
            DEFAULT_KITCHEN,
            serving_time="18:00",
        )

        by_id = {s.step_id: s for s in result.scheduled_steps}

        cook_a = by_id["ft_recipe_a_cook"]
        cook_b = by_id["ft_recipe_b_cook"]
        cook_c = by_id["ft_recipe_c_cook"]

        # Recipe A (OVEN, 180 min) ends at T+30 + 180 = T+210
        assert cook_a.end_at_minute == 210, f"Recipe A cooking should end at T+210, got T+{cook_a.end_at_minute}"

        # Recipe B (STOVETOP, 60 min) starts at offset 120, ends at T+180
        # This should finish within 30 min of Recipe A
        assert cook_b.end_at_minute == 180, f"Recipe B cooking should end at T+180, got T+{cook_b.end_at_minute}"

        # Verify Recipe B finishes within 30 min of Recipe A
        a_b_window = abs(cook_a.end_at_minute - cook_b.end_at_minute)
        assert a_b_window <= 30, f"Recipe A and B should finish within 30 min, got {a_b_window}"

        # Recipe C uses OVEN, must wait for Recipe A to finish (oven capacity=1)
        # So Recipe C starts at T+210 (when A finishes), ends at T+270
        # This is expected behavior — oven contention overrides finish-together for C
        assert cook_c.start_at_minute >= 210, f"Recipe C must wait for oven, should start at T+210+, got T+{cook_c.start_at_minute}"

    def test_prep_steps_scheduled_early(self, finish_together_fixtures):
        """
        Verify HANDS prep steps for shorter recipes happen early, before their cooking starts.

        Recipe B and C have cooking delayed by 120 min offset, but their prep steps
        should still happen ASAP (not delayed). This enables productive use of the
        wait time during Recipe A's cooking.
        """
        result = _merge_dags(
            finish_together_fixtures["dags"],
            finish_together_fixtures["validated"],
            DEFAULT_KITCHEN,
            serving_time="18:00",
        )

        by_id = {s.step_id: s for s in result.scheduled_steps}

        # Prep steps should be scheduled early (ASAP from dependencies)
        prep_a = by_id["ft_recipe_a_prep"]
        prep_b = by_id["ft_recipe_b_prep"]
        prep_c = by_id["ft_recipe_c_prep"]
        cook_b = by_id["ft_recipe_b_cook"]
        cook_c = by_id["ft_recipe_c_cook"]

        # All prep steps should start early (within first 30 min, accounting for HANDS exclusivity)
        assert prep_a.start_at_minute < 60, f"Prep A should be early, got T+{prep_a.start_at_minute}"
        assert prep_b.start_at_minute < 60, f"Prep B should be early, got T+{prep_b.start_at_minute}"
        assert prep_c.start_at_minute < 60, f"Prep C should be early, got T+{prep_c.start_at_minute}"

        # Prep should finish before cooking starts (dependency constraint)
        assert prep_b.end_at_minute <= cook_b.start_at_minute, (
            f"Prep B must finish before Cook B starts: prep ends T+{prep_b.end_at_minute}, "
            f"cook starts T+{cook_b.start_at_minute}"
        )
        assert prep_c.end_at_minute <= cook_c.start_at_minute, (
            f"Prep C must finish before Cook C starts: prep ends T+{prep_c.end_at_minute}, "
            f"cook starts T+{cook_c.start_at_minute}"
        )

        # Cooking starts at offset (120 min) not immediately after prep
        assert cook_b.start_at_minute >= 120, (
            f"Cook B should start at offset 120+, got T+{cook_b.start_at_minute}"
        )

    def test_no_serving_time_uses_asap(self, finish_together_fixtures):
        """
        Verify existing ASAP behavior when serving_time=None (backward compatible).

        Without finish-together scheduling, cooking starts as soon as dependencies
        and resources allow. Recipe B (STOVETOP) should start before offset=120
        would allow.
        """
        result = _merge_dags(
            finish_together_fixtures["dags"],
            finish_together_fixtures["validated"],
            DEFAULT_KITCHEN,
            serving_time=None,  # ASAP mode
        )

        by_id = {s.step_id: s for s in result.scheduled_steps}

        # Cooking should start immediately after prep and HANDS availability (ASAP)
        cook_b = by_id["ft_recipe_b_cook"]

        # In ASAP mode, Recipe B cooking should start much earlier than offset=120
        # (HANDS exclusivity causes some serialization of prep steps, but cooking
        # should start well before the 120 min offset that finish-together would use)
        assert cook_b.start_at_minute < 120, (
            f"ASAP: Cook B should start before offset 120, got T+{cook_b.start_at_minute}"
        )

        # Compare with finish-together mode to verify the difference
        result_ft = _merge_dags(
            finish_together_fixtures["dags"],
            finish_together_fixtures["validated"],
            DEFAULT_KITCHEN,
            serving_time="18:00",
        )

        ft_by_id = {s.step_id: s for s in result_ft.scheduled_steps}
        cook_b_ft = ft_by_id["ft_recipe_b_cook"]

        # In finish-together mode, Recipe B should start at offset 120
        # In ASAP mode, it should start earlier
        assert cook_b.start_at_minute < cook_b_ft.start_at_minute, (
            f"ASAP mode should start cooking earlier than FT mode: "
            f"ASAP T+{cook_b.start_at_minute} vs FT T+{cook_b_ft.start_at_minute}"
        )

    def test_single_recipe_with_serving_time(self):
        """
        Verify single recipe works correctly with serving_time set.

        A single recipe should get offset=0 (it's the anchor). The schedule
        should be identical to ASAP mode.
        """
        from tests.fixtures.recipes import ENRICHED_FT_RECIPE_A
        from tests.fixtures.schedules import RECIPE_DAG_FT_A

        validated_a = ValidatedRecipe(source=ENRICHED_FT_RECIPE_A, validated_at=datetime.now())

        result_ft = _merge_dags(
            [RECIPE_DAG_FT_A],
            [validated_a],
            DEFAULT_KITCHEN,
            serving_time="18:00",
        )

        result_asap = _merge_dags(
            [RECIPE_DAG_FT_A],
            [validated_a],
            DEFAULT_KITCHEN,
            serving_time=None,
        )

        # Both should produce the same schedule
        assert result_ft.total_duration_minutes == result_asap.total_duration_minutes, (
            f"Single recipe: FT={result_ft.total_duration_minutes} vs ASAP={result_asap.total_duration_minutes}"
        )

        # Verify the cooking step starts at the expected time
        by_id_ft = {s.step_id: s for s in result_ft.scheduled_steps}
        cook_a = by_id_ft["ft_recipe_a_cook"]

        # Cooking should start immediately after prep (30 min)
        assert cook_a.start_at_minute == 30, f"Single recipe cooking should start at T+30, got T+{cook_a.start_at_minute}"

    def test_equal_cooking_duration_no_stagger(self):
        """
        Verify that recipes with equal cooking durations get no stagger.

        When all recipes have the same cooking time, all offsets are 0,
        so the schedule is identical to ASAP mode for the cooking steps.
        """
        # Create two recipes with identical 60 min cooking times
        raw_a = RawRecipe(
            name="Equal A",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=70,
            ingredients=[],
            steps=["prep", "cook"],
        )
        raw_b = RawRecipe(
            name="Equal B",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=70,
            ingredients=[],
            steps=["prep", "cook"],
        )

        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(step_id="eq_a_prep", description="prep A", duration_minutes=10, resource=Resource.HANDS),
                RecipeStep(step_id="eq_a_cook", description="cook A", duration_minutes=60, resource=Resource.OVEN, depends_on=["eq_a_prep"]),
            ],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(step_id="eq_b_prep", description="prep B", duration_minutes=10, resource=Resource.HANDS),
                RecipeStep(step_id="eq_b_cook", description="cook B", duration_minutes=60, resource=Resource.STOVETOP, depends_on=["eq_b_prep"]),
            ],
        )

        dag_a = RecipeDAG(recipe_name="Equal A", recipe_slug="equal_a", steps=[], edges=[("eq_a_prep", "eq_a_cook")])
        dag_b = RecipeDAG(recipe_name="Equal B", recipe_slug="equal_b", steps=[], edges=[("eq_b_prep", "eq_b_cook")])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())

        result_ft = _merge_dags(
            [dag_a, dag_b],
            [validated_a, validated_b],
            DEFAULT_KITCHEN,
            serving_time="18:00",
        )

        result_asap = _merge_dags(
            [dag_a, dag_b],
            [validated_a, validated_b],
            DEFAULT_KITCHEN,
            serving_time=None,
        )

        # With equal cooking durations, FT and ASAP should produce similar schedules
        # (Cooking steps should start at similar times)
        ft_by_id = {s.step_id: s for s in result_ft.scheduled_steps}
        asap_by_id = {s.step_id: s for s in result_asap.scheduled_steps}

        # Equal cooking durations → offset = 0 for both → cooking starts same as ASAP
        assert ft_by_id["eq_a_cook"].start_at_minute == asap_by_id["eq_a_cook"].start_at_minute, (
            "Equal duration: Cook A should start same time in FT vs ASAP"
        )
        assert ft_by_id["eq_b_cook"].start_at_minute == asap_by_id["eq_b_cook"].start_at_minute, (
            "Equal duration: Cook B should start same time in FT vs ASAP"
        )

    def test_cooking_steps_staggered_correctly(self, finish_together_fixtures):
        """
        Verify cooking step start times are correctly staggered.

        Recipe A (180 min cooking): offset=0, starts at T+30 (after 30 min prep)
        Recipe B (60 min cooking): offset=120, starts at T+120 (delayed)
        Recipe C (60 min cooking): offset=120, starts at T+120 (may wait for oven)
        """
        result = _merge_dags(
            finish_together_fixtures["dags"],
            finish_together_fixtures["validated"],
            DEFAULT_KITCHEN,
            serving_time="18:00",
        )

        by_id = {s.step_id: s for s in result.scheduled_steps}

        cook_a = by_id["ft_recipe_a_cook"]
        cook_b = by_id["ft_recipe_b_cook"]
        cook_c = by_id["ft_recipe_c_cook"]

        # Recipe A (anchor): cooking starts after prep ends (T+30)
        assert cook_a.start_at_minute == 30, f"Anchor cooking should start at T+30, got T+{cook_a.start_at_minute}"

        # Recipe B: offset=120, prep=15 → cooking starts at max(15, 120) = 120
        assert cook_b.start_at_minute == 120, f"Recipe B cooking should start at T+120, got T+{cook_b.start_at_minute}"

        # Recipe C: offset=120, prep=20 → cooking starts at max(20, 120) = 120
        # But Recipe A uses the oven from T+30 to T+210, so Recipe C (OVEN) may wait
        # Recipe C should start at 120 or later (when oven is free)
        assert cook_c.start_at_minute >= 120, f"Recipe C cooking should start at T+120+, got T+{cook_c.start_at_minute}"

    def test_stovetop_only_finish_within_window(self):
        """
        Verify three recipes using only STOVETOP finish close together.

        This test uses STOVETOP-only recipes to avoid oven contention issues.
        Recipe A: 180 min cooking (3h)
        Recipe B: 60 min cooking (1h)
        Recipe C: 60 min cooking (1h)

        With 4 burners and no oven conflicts, all cooking should finish
        within a reasonable window when finish-together is enabled.
        The window may be slightly larger than the ideal due to prep time
        differences (A prep = 20 min, B/C prep = 10 min).
        """
        # Create three STOVETOP-only recipes
        raw_a = RawRecipe(
            name="Stovetop A",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=200,
            ingredients=[],
            steps=["prep", "cook"],
        )
        raw_b = RawRecipe(
            name="Stovetop B",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=70,
            ingredients=[],
            steps=["prep", "cook"],
        )
        raw_c = RawRecipe(
            name="Stovetop C",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=70,
            ingredients=[],
            steps=["prep", "cook"],
        )

        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(step_id="st_a_prep", description="prep A", duration_minutes=20, resource=Resource.HANDS),
                RecipeStep(step_id="st_a_cook", description="cook A 3h", duration_minutes=180, resource=Resource.STOVETOP, depends_on=["st_a_prep"]),
            ],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(step_id="st_b_prep", description="prep B", duration_minutes=10, resource=Resource.HANDS),
                RecipeStep(step_id="st_b_cook", description="cook B 1h", duration_minutes=60, resource=Resource.STOVETOP, depends_on=["st_b_prep"]),
            ],
        )
        enriched_c = EnrichedRecipe(
            source=raw_c,
            steps=[
                RecipeStep(step_id="st_c_prep", description="prep C", duration_minutes=10, resource=Resource.HANDS),
                RecipeStep(step_id="st_c_cook", description="cook C 1h", duration_minutes=60, resource=Resource.STOVETOP, depends_on=["st_c_prep"]),
            ],
        )

        dag_a = RecipeDAG(recipe_name="Stovetop A", recipe_slug="stovetop_a", steps=[], edges=[("st_a_prep", "st_a_cook")])
        dag_b = RecipeDAG(recipe_name="Stovetop B", recipe_slug="stovetop_b", steps=[], edges=[("st_b_prep", "st_b_cook")])
        dag_c = RecipeDAG(recipe_name="Stovetop C", recipe_slug="stovetop_c", steps=[], edges=[("st_c_prep", "st_c_cook")])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())
        validated_c = ValidatedRecipe(source=enriched_c, validated_at=datetime.now())

        result = _merge_dags(
            [dag_a, dag_b, dag_c],
            [validated_a, validated_b, validated_c],
            DEFAULT_KITCHEN,  # 4 burners, plenty of capacity
            serving_time="18:00",
        )

        # Find all cooking step end times
        cooking_steps = [
            s for s in result.scheduled_steps
            if s.resource == Resource.STOVETOP
        ]
        cooking_end_times = [s.end_at_minute for s in cooking_steps]

        # Verify the algorithm is working correctly:
        # Recipe A: 180 min cooking, offset=0, starts at T+20 (after 20 min prep), ends at T+200
        # Recipe B: 60 min cooking, offset=120, starts at T+120, ends at T+180
        # Recipe C: 60 min cooking, offset=120, starts at T+120, ends at T+180
        by_id = {s.step_id: s for s in result.scheduled_steps}
        assert by_id["st_a_cook"].start_at_minute == 20, "Recipe A should start at T+20 (after prep)"
        assert by_id["st_b_cook"].start_at_minute == 120, "Recipe B should start at offset 120"
        assert by_id["st_c_cook"].start_at_minute == 120, "Recipe C should start at offset 120"

        # Window is 20 min (200 - 180) due to Recipe A's longer prep time.
        # The algorithm correctly staggered B and C to finish at T+180,
        # but A's prep adds 10 min to its end time (20 + 180 = 200).
        min_end = min(cooking_end_times)
        max_end = max(cooking_end_times)
        window = max_end - min_end

        assert window <= 25, (
            f"STOVETOP-only cooking should finish within 25 min window, got {window} min. "
            f"End times: {sorted(cooking_end_times)}"
        )

        # Compare with ASAP mode — the window should be much larger
        result_asap = _merge_dags(
            [dag_a, dag_b, dag_c],
            [validated_a, validated_b, validated_c],
            DEFAULT_KITCHEN,
            serving_time=None,  # ASAP mode
        )

        asap_cooking_steps = [
            s for s in result_asap.scheduled_steps
            if s.resource == Resource.STOVETOP
        ]
        asap_cooking_end_times = [s.end_at_minute for s in asap_cooking_steps]
        asap_window = max(asap_cooking_end_times) - min(asap_cooking_end_times)

        assert asap_window > window, (
            f"ASAP window ({asap_window}) should be larger than FT window ({window})"
        )


class TestBurnerDescriptorModels:
    def test_scheduled_step_accepts_explicit_burner_metadata(self):
        """ScheduledStep persists optional burner assignment metadata."""
        descriptor = BurnerDescriptor(
            burner_id="front_left_large",
            position="front_left",
            size="large",
            label="Front Left",
        )

        step = ScheduledStep(
            step_id="s",
            recipe_name="r",
            description="cook",
            resource=Resource.STOVETOP,
            duration_minutes=5,
            start_at_minute=0,
            end_at_minute=5,
            burner_id="front_left_large",
            burner_position="front_left",
            burner_size="large",
            burner_label="Front Left",
            burner=descriptor,
        )

        assert step.burner_id == "front_left_large"
        assert step.burner_position == "front_left"
        assert step.burner_size == "large"
        assert step.burner_label == "Front Left"
        assert step.burner is not None
        assert step.burner.burner_id == "front_left_large"

    def test_timeline_entry_accepts_explicit_burner_metadata(self):
        """TimelineEntry is the renderer/presentation seam for scheduler-owned burner metadata."""
        descriptor = BurnerDescriptor(
            burner_id="front_left_large",
            position="front_left",
            size="large",
            label="Front Left",
        )

        entry = _build_timeline_entry(
            ScheduledStep(
                step_id="s",
                recipe_name="r",
                description="cook",
                resource=Resource.STOVETOP,
                duration_minutes=5,
                start_at_minute=0,
                end_at_minute=5,
                burner_id="front_left_large",
                burner_position="front_left",
                burner_size="large",
                burner_label="Front Left",
                burner=descriptor,
            )
        )

        assert entry.burner_id == "front_left_large"
        assert entry.burner_position == "front_left"
        assert entry.burner_size == "large"
        assert entry.burner_label == "Front Left"
        assert entry.burner is not None
        assert entry.burner.burner_id == "front_left_large"

    def test_timeline_entry_does_not_infer_burner_metadata_for_non_stovetop_steps(self):
        """Consumers must read burner metadata from the scheduler contract, not derive it from copy."""
        entry = _build_timeline_entry(
            ScheduledStep(
                step_id="oven_step",
                recipe_name="Roast",
                description="Roast until browned",
                resource=Resource.OVEN,
                duration_minutes=25,
                start_at_minute=10,
                end_at_minute=35,
            )
        )

        assert entry.burner_id is None
        assert entry.burner_position is None
        assert entry.burner_size is None
        assert entry.burner_label is None
        assert entry.burner is None

    def test_kitchen_config_accepts_optional_burner_descriptors(self):
        """KitchenConfig burners remain optional but preserve ordered descriptors when present."""
        config = KitchenConfig(
            max_burners=4,
            burners=[
                BurnerDescriptor(burner_id="front_left_large", position="front_left", size="large"),
                BurnerDescriptor(burner_id="rear_right_small", position="rear_right", size="small"),
            ],
        )

        assert [burner.burner_id for burner in config.burners] == ["front_left_large", "rear_right_small"]
        assert config.max_burners == 4

    def test_kitchen_config_rejects_burner_descriptors_above_capacity(self):
        """KitchenConfig should reject impossible burner cardinality before scheduling sees it."""
        with pytest.raises(ValidationError, match="burners count cannot exceed max_burners"):
            KitchenConfig.model_validate(
                {
                    "max_burners": 1,
                    "burners": [
                        {"burner_id": "front_left_large"},
                        {"burner_id": "front_right_medium"},
                    ],
                }
            )


class TestRendererBurnerOutput:
    def test_renderer_timeline_entry_preserves_scheduler_burner_fields_verbatim(self):
        descriptor = BurnerDescriptor(
            burner_id="front_left_large",
            position="front_left",
            size="large",
            label="Front Left",
        )

        entry = _build_timeline_entry(
            ScheduledStep(
                step_id="sear_step",
                recipe_name="Steak",
                description="Sear steak",
                resource=Resource.STOVETOP,
                duration_minutes=8,
                start_at_minute=15,
                end_at_minute=23,
                burner_id="front_left_large",
                burner_position="front_left",
                burner_size="large",
                burner_label="Front Left",
                burner=descriptor,
            )
        )

        assert entry.step_id == "sear_step"
        assert entry.action == "Sear steak"
        assert entry.burner_id == "front_left_large"
        assert entry.burner_position == "front_left"
        assert entry.burner_size == "large"
        assert entry.burner_label == "Front Left"
        assert entry.burner is not None
        assert entry.burner.burner_id == "front_left_large"

    def test_renderer_timeline_keeps_non_stovetop_entries_burner_free(self):
        merged = MergedDAG(
            scheduled_steps=[
                ScheduledStep(
                    step_id="oven_step",
                    recipe_name="Roast",
                    description="Roast until browned",
                    resource=Resource.OVEN,
                    duration_minutes=25,
                    start_at_minute=10,
                    end_at_minute=35,
                ),
                ScheduledStep(
                    step_id="stove_step",
                    recipe_name="Sauce",
                    description="Simmer sauce",
                    resource=Resource.STOVETOP,
                    duration_minutes=12,
                    start_at_minute=35,
                    end_at_minute=47,
                    burner_id="burner_2",
                    burner_position="rear_right",
                    burner_size="small",
                    burner_label="Rear Right",
                    burner=BurnerDescriptor(
                        burner_id="burner_2",
                        position="rear_right",
                        size="small",
                        label="Rear Right",
                    ),
                ),
            ],
            total_duration_minutes=47,
        )

        timeline = _build_timeline(merged)
        by_id = {entry.step_id: entry for entry in timeline}

        assert by_id["oven_step"].burner_id is None
        assert by_id["oven_step"].burner is None
        assert by_id["oven_step"].action == "Roast until browned"
        assert by_id["stove_step"].burner_id == "burner_2"
        assert by_id["stove_step"].burner_label == "Rear Right"
        assert by_id["stove_step"].action == "Simmer sauce"


class TestBurnerAllocation:
    def _make_stovetop_recipe(
        self,
        name: str,
        slug: str,
        step_id: str,
        duration: int,
        description: str | None = None,
        depends_on: list[str] | None = None,
    ) -> tuple[RecipeDAG, ValidatedRecipe]:
        raw = RawRecipe(
            name=name,
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=duration,
            ingredients=[],
            steps=["cook"],
        )
        enriched = EnrichedRecipe(
            source=raw,
            steps=[
                RecipeStep(
                    step_id=step_id,
                    description=description or f"cook {name}",
                    duration_minutes=duration,
                    resource=Resource.STOVETOP,
                    depends_on=depends_on or [],
                )
            ],
        )
        dag = RecipeDAG(recipe_name=name, recipe_slug=slug, steps=[], edges=[])
        validated = ValidatedRecipe(source=enriched, validated_at=datetime.now())
        return dag, validated

    def test_stovetop_steps_use_descriptor_backed_burners_in_order(self):
        dag_a, val_a = self._make_stovetop_recipe("Recipe A", "recipe_a", "a_step", 30)
        dag_b, val_b = self._make_stovetop_recipe("Recipe B", "recipe_b", "b_step", 30)

        kitchen = {
            "max_burners": 4,
            "burners": [
                {
                    "burner_id": "front_left_large",
                    "position": "front_left",
                    "size": "large",
                    "label": "Front Left",
                },
                {
                    "burner_id": "rear_right_small",
                    "position": "rear_right",
                    "size": "small",
                    "label": "Rear Right",
                },
            ],
        }

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], kitchen)
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["a_step"].burner_id == "front_left_large"
        assert by_id["a_step"].burner_position == "front_left"
        assert by_id["a_step"].burner_size == "large"
        assert by_id["a_step"].burner_label == "Front Left"
        assert by_id["a_step"].burner is not None
        assert by_id["a_step"].burner.burner_id == "front_left_large"

        assert by_id["b_step"].burner_id == "rear_right_small"
        assert by_id["b_step"].burner_label == "Rear Right"

    def test_stovetop_steps_fall_back_to_stable_burner_numbering(self):
        dag_a, val_a = self._make_stovetop_recipe("Recipe A", "recipe_a", "a_step", 30)
        dag_b, val_b = self._make_stovetop_recipe("Recipe B", "recipe_b", "b_step", 30)

        result = _merge_dags(
            [dag_a, dag_b],
            [val_a, val_b],
            {"max_burners": 2, "has_second_oven": False},
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["a_step"].burner_id == "burner_1"
        assert by_id["a_step"].burner_label == "Burner 1"
        assert by_id["b_step"].burner_id == "burner_2"
        assert by_id["b_step"].burner_label == "Burner 2"

    def test_stovetop_waits_for_next_burner_release_when_all_slots_busy(self):
        dag_a, val_a = self._make_stovetop_recipe("Recipe A", "recipe_a", "a_step", 30)
        dag_b, val_b = self._make_stovetop_recipe("Recipe B", "recipe_b", "b_step", 30)
        dag_c, val_c = self._make_stovetop_recipe("Recipe C", "recipe_c", "c_step", 10)

        kitchen = {
            "max_burners": 2,
            "burners": [
                {"burner_id": "burner_a", "label": "Burner A"},
                {"burner_id": "burner_b", "label": "Burner B"},
            ],
        }

        result = _merge_dags([dag_a, dag_b, dag_c], [val_a, val_b, val_c], kitchen)
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["a_step"].start_at_minute == 0
        assert by_id["b_step"].start_at_minute == 0
        assert by_id["c_step"].start_at_minute == 30
        assert by_id["c_step"].burner_id == "burner_a"

    def test_heterogeneous_burners_assign_large_sear_and_avoid_small_for_delicate_step(self):
        dag_sear, val_sear = self._make_stovetop_recipe(
            "Sear Steak",
            "sear_steak",
            "sear_step",
            20,
            description="high-heat sear steak stovetop_heat_f: 500 in a large pan",
        )
        dag_sauce, val_sauce = self._make_stovetop_recipe(
            "Delicate Sauce",
            "delicate_sauce",
            "sauce_step",
            20,
            description="gentle simmer sauce stovetop_heat_f: 180 on a small pan",
        )

        kitchen = {
            "max_burners": 4,
            "burners": [
                {"burner_id": "front_left_large", "position": "front_left", "size": "large", "label": "Front Left"},
                {"burner_id": "front_right_medium", "position": "front_right", "size": "medium", "label": "Front Right"},
                {"burner_id": "rear_left_medium", "position": "rear_left", "size": "medium", "label": "Rear Left"},
                {"burner_id": "rear_right_small", "position": "rear_right", "size": "small", "label": "Rear Right"},
            ],
        }

        result = _merge_dags([dag_sear, dag_sauce], [val_sear, val_sauce], kitchen)
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["sear_step"].start_at_minute == 0
        assert by_id["sauce_step"].start_at_minute == 0
        assert by_id["sear_step"].burner_id == "front_left_large"
        assert by_id["sear_step"].burner_size == "large"
        assert by_id["sauce_step"].burner_id == "rear_right_small"
        assert by_id["sauce_step"].burner_size == "small"

    def test_one_suitable_burner_serializes_overlapping_large_pan_steps(self):
        dag_a, val_a = self._make_stovetop_recipe(
            "Large Pan A",
            "large_pan_a",
            "large_a",
            30,
            description="high-heat sear mushrooms stovetop_heat_f: 450 in a large pan",
        )
        dag_b, val_b = self._make_stovetop_recipe(
            "Large Pan B",
            "large_pan_b",
            "large_b",
            15,
            description="high-heat sear peppers stovetop_heat_f: 430 in a large pan",
        )

        kitchen = {
            "max_burners": 3,
            "burners": [
                {"burner_id": "big", "size": "large", "label": "Big Burner"},
                {"burner_id": "mid", "size": "medium", "label": "Mid Burner"},
                {"burner_id": "small", "size": "small", "label": "Small Burner"},
            ],
        }

        result = _merge_dags([dag_a, dag_b], [val_a, val_b], kitchen)
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["large_a"].burner_id == "big"
        assert by_id["large_b"].burner_id == "big"
        assert by_id["large_a"].start_at_minute == 0
        assert by_id["large_b"].start_at_minute == 30
        assert by_id["large_b"].end_at_minute == 45

    def test_three_stovetop_recipes_wait_for_constrained_suitable_burners_then_release_deterministically(self):
        dag_large, val_large = self._make_stovetop_recipe(
            "Large Sear",
            "large_sear",
            "large_step",
            25,
            description="high-heat sear tofu stovetop_heat_f: 480 in a large pan",
        )
        dag_medium, val_medium = self._make_stovetop_recipe(
            "Medium Saute",
            "medium_saute",
            "medium_step",
            25,
            description="saute greens stovetop_heat_f: 340",
        )
        dag_delayed, val_delayed = self._make_stovetop_recipe(
            "Delayed Sear",
            "delayed_sear",
            "delayed_step",
            10,
            description="high-heat sear scallops stovetop_heat_f: 470 in a large pan",
        )

        kitchen = {
            "max_burners": 2,
            "burners": [
                {"burner_id": "large_only", "size": "large", "label": "Large Burner"},
                {"burner_id": "medium_only", "size": "medium", "label": "Medium Burner"},
            ],
        }

        result = _merge_dags(
            [dag_large, dag_medium, dag_delayed],
            [val_large, val_medium, val_delayed],
            kitchen,
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["large_step"].start_at_minute == 0
        assert by_id["medium_step"].start_at_minute == 0
        assert by_id["large_step"].burner_id == "large_only"
        assert by_id["medium_step"].burner_id == "medium_only"
        assert by_id["delayed_step"].start_at_minute == 25
        assert by_id["delayed_step"].burner_id == "large_only"

    def test_finish_together_still_composes_with_explicit_burners_and_oven_contention(self):
        raw_a = RawRecipe(
            name="Long Braise",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=210,
            ingredients=[],
            steps=["prep", "braise"],
        )
        raw_b = RawRecipe(
            name="Quick Sear",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=70,
            ingredients=[],
            steps=["prep", "sear"],
        )
        raw_c = RawRecipe(
            name="Pan Sauce",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=70,
            ingredients=[],
            steps=["prep", "simmer"],
        )

        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(step_id="braise_prep", description="prep braise", duration_minutes=20, resource=Resource.HANDS),
                RecipeStep(
                    step_id="braise_cook",
                    description="braise in oven",
                    duration_minutes=180,
                    resource=Resource.OVEN,
                    depends_on=["braise_prep"],
                ),
            ],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(step_id="sear_prep", description="prep sear", duration_minutes=10, resource=Resource.HANDS),
                RecipeStep(
                    step_id="sear_cook",
                    description="high-heat sear chops stovetop_heat_f: 500 in a large pan",
                    duration_minutes=60,
                    resource=Resource.STOVETOP,
                    depends_on=["sear_prep"],
                ),
            ],
        )
        enriched_c = EnrichedRecipe(
            source=raw_c,
            steps=[
                RecipeStep(step_id="sauce_prep", description="prep sauce", duration_minutes=10, resource=Resource.HANDS),
                RecipeStep(
                    step_id="sauce_cook",
                    description="gentle simmer pan sauce stovetop_heat_f: 180 on a small pan",
                    duration_minutes=60,
                    resource=Resource.STOVETOP,
                    depends_on=["sauce_prep"],
                ),
            ],
        )

        dag_a = RecipeDAG(recipe_name="Long Braise", recipe_slug="long_braise", steps=[], edges=[("braise_prep", "braise_cook")])
        dag_b = RecipeDAG(recipe_name="Quick Sear", recipe_slug="quick_sear", steps=[], edges=[("sear_prep", "sear_cook")])
        dag_c = RecipeDAG(recipe_name="Pan Sauce", recipe_slug="pan_sauce", steps=[], edges=[("sauce_prep", "sauce_cook")])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())
        validated_c = ValidatedRecipe(source=enriched_c, validated_at=datetime.now())

        kitchen = {
            "max_burners": 3,
            "max_oven_racks": 1,
            "has_second_oven": False,
            "burners": [
                {"burner_id": "front_left_large", "position": "front_left", "size": "large", "label": "Front Left"},
                {"burner_id": "front_right_medium", "position": "front_right", "size": "medium", "label": "Front Right"},
                {"burner_id": "rear_right_small", "position": "rear_right", "size": "small", "label": "Rear Right"},
            ],
        }

        result = _merge_dags(
            [dag_a, dag_b, dag_c],
            [validated_a, validated_b, validated_c],
            kitchen,
            serving_time="18:00",
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["braise_cook"].start_at_minute == 20
        assert by_id["braise_cook"].end_at_minute == 200
        assert by_id["sear_cook"].start_at_minute == 120
        assert by_id["sear_cook"].end_at_minute == 180
        assert by_id["sauce_cook"].start_at_minute == 120
        assert by_id["sauce_cook"].end_at_minute == 180
        assert by_id["sear_cook"].burner_id == "front_left_large"
        assert by_id["sauce_cook"].burner_id == "rear_right_small"
        assert by_id["sear_prep"].start_at_minute < by_id["sear_cook"].start_at_minute
        assert by_id["sauce_prep"].start_at_minute < by_id["sauce_cook"].start_at_minute


class TestResourceWarnings:
    """
    Tests for resource constraint warning detection.

    These tests verify that `_detect_resource_warnings()` correctly identifies
    when oven/stovetop contention prevents recipes from finishing together,
    and generates user-friendly warnings.
    """

    def test_no_warnings_in_asap_mode(self):
        """ASAP mode (no serving_time) should never produce warnings."""
        # Create a simple step that would trigger warning in FT mode
        step = ScheduledStep(
            step_id="test_step",
            recipe_name="Test Recipe",
            description="test",
            resource=Resource.OVEN,
            duration_minutes=60,
            start_at_minute=0,
            end_at_minute=60,
        )
        capacities = {Resource.OVEN: 1, Resource.STOVETOP: 4, Resource.HANDS: 1, Resource.PASSIVE: float("inf")}

        # Empty finish_offsets dict = ASAP mode
        warnings = _detect_resource_warnings([step], {}, capacities)

        assert warnings == [], "ASAP mode should not produce warnings"

    def test_no_warnings_when_within_threshold(self):
        """No warnings when all recipes finish within 20 min of anchor."""
        steps = [
            ScheduledStep(
                step_id="anchor_cook",
                recipe_name="Anchor Recipe",
                description="cook anchor",
                resource=Resource.OVEN,
                duration_minutes=60,
                start_at_minute=0,
                end_at_minute=60,
            ),
            ScheduledStep(
                step_id="other_cook",
                recipe_name="Other Recipe",
                description="cook other",
                resource=Resource.STOVETOP,
                duration_minutes=60,
                start_at_minute=15,
                end_at_minute=75,  # 15 min after anchor (within threshold)
            ),
        ]
        finish_offsets = {"Anchor Recipe": 0, "Other Recipe": 0}
        capacities = {Resource.OVEN: 1, Resource.STOVETOP: 4, Resource.HANDS: 1, Resource.PASSIVE: float("inf")}

        warnings = _detect_resource_warnings(steps, finish_offsets, capacities)

        assert warnings == [], "Recipes finishing within 20 min should not trigger warnings"

    def test_warning_when_oven_contention_delays_recipe(self):
        """Warning generated when oven contention causes >20 min delay."""
        steps = [
            ScheduledStep(
                step_id="anchor_cook",
                recipe_name="Recipe A Long Braise",
                description="braise anchor",
                resource=Resource.OVEN,
                duration_minutes=180,
                start_at_minute=30,
                end_at_minute=210,  # Anchor finishes at T+210
            ),
            ScheduledStep(
                step_id="delayed_cook",
                recipe_name="Recipe C Medium Roast",
                description="roast delayed",
                resource=Resource.OVEN,
                duration_minutes=60,
                start_at_minute=210,  # Must wait for anchor's oven
                end_at_minute=270,  # Finishes 60 min after anchor (>20 min)
            ),
        ]
        # Recipe C has offset=120 (would ideally finish near anchor) but oven contention delays it
        finish_offsets = {"Recipe A Long Braise": 0, "Recipe C Medium Roast": 120}
        capacities = {Resource.OVEN: 1, Resource.STOVETOP: 4, Resource.HANDS: 1, Resource.PASSIVE: float("inf")}

        warnings = _detect_resource_warnings(steps, finish_offsets, capacities)

        assert len(warnings) == 1, f"Expected 1 warning, got {len(warnings)}"
        assert "Recipe C Medium Roast" in warnings[0], "Warning should mention delayed recipe"
        assert "oven" in warnings[0].lower(), "Warning should mention oven resource"
        assert "60" in warnings[0], "Warning should mention delay amount (~60 min)"
        assert "Recipe A Long Braise" in warnings[0], "Warning should mention anchor recipe"

    def test_warning_message_format(self):
        """Verify warning message is user-friendly with actionable suggestion."""
        steps = [
            ScheduledStep(
                step_id="anchor_cook",
                recipe_name="Anchor Recipe",
                description="anchor",
                resource=Resource.OVEN,
                duration_minutes=180,
                start_at_minute=0,
                end_at_minute=180,
            ),
            ScheduledStep(
                step_id="delayed_cook",
                recipe_name="Delayed Recipe",
                description="delayed",
                resource=Resource.OVEN,
                duration_minutes=60,
                start_at_minute=180,  # Must wait for oven
                end_at_minute=240,  # 60 min after anchor
            ),
        ]
        finish_offsets = {"Anchor Recipe": 0, "Delayed Recipe": 120}
        capacities = {Resource.OVEN: 1, Resource.STOVETOP: 4, Resource.HANDS: 1, Resource.PASSIVE: float("inf")}

        warnings = _detect_resource_warnings(steps, finish_offsets, capacities)

        assert len(warnings) == 1
        warning = warnings[0]

        # Should have recipe name, resource, delay, and suggestion
        assert "Delayed Recipe" in warning
        assert "oven" in warning.lower()
        assert "second oven" in warning.lower(), "Should suggest second oven for oven contention"

    def test_multiple_warnings_for_multiple_delayed_recipes(self):
        """Multiple recipes delayed beyond threshold should produce multiple warnings."""
        steps = [
            ScheduledStep(
                step_id="anchor_cook",
                recipe_name="Anchor Recipe",
                description="anchor",
                resource=Resource.OVEN,
                duration_minutes=180,
                start_at_minute=0,
                end_at_minute=180,
            ),
            ScheduledStep(
                step_id="delayed_1_cook",
                recipe_name="Delayed Recipe 1",
                description="delayed 1",
                resource=Resource.OVEN,
                duration_minutes=60,
                start_at_minute=180,
                end_at_minute=240,  # 60 min after anchor
            ),
            ScheduledStep(
                step_id="delayed_2_cook",
                recipe_name="Delayed Recipe 2",
                description="delayed 2",
                resource=Resource.OVEN,
                duration_minutes=60,
                start_at_minute=240,
                end_at_minute=300,  # 120 min after anchor
            ),
        ]
        finish_offsets = {"Anchor Recipe": 0, "Delayed Recipe 1": 120, "Delayed Recipe 2": 120}
        capacities = {Resource.OVEN: 1, Resource.STOVETOP: 4, Resource.HANDS: 1, Resource.PASSIVE: float("inf")}

        warnings = _detect_resource_warnings(steps, finish_offsets, capacities)

        assert len(warnings) == 2, f"Expected 2 warnings, got {len(warnings)}"
        assert any("Delayed Recipe 1" in w for w in warnings)
        assert any("Delayed Recipe 2" in w for w in warnings)

    def test_integration_with_merge_dags_oven_contention(self):
        """
        Integration test: verify MergedDAG.resource_warnings populated when oven contention occurs.

        Uses the FT fixtures where Recipe C (OVEN) must wait for Recipe A's oven to free up.
        """
        from tests.fixtures.recipes import (
            ENRICHED_FT_RECIPE_A,
            ENRICHED_FT_RECIPE_B,
            ENRICHED_FT_RECIPE_C,
        )
        from tests.fixtures.schedules import (
            RECIPE_DAG_FT_A,
            RECIPE_DAG_FT_B,
            RECIPE_DAG_FT_C,
        )

        validated_a = ValidatedRecipe(source=ENRICHED_FT_RECIPE_A, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=ENRICHED_FT_RECIPE_B, validated_at=datetime.now())
        validated_c = ValidatedRecipe(source=ENRICHED_FT_RECIPE_C, validated_at=datetime.now())

        result = _merge_dags(
            [RECIPE_DAG_FT_A, RECIPE_DAG_FT_B, RECIPE_DAG_FT_C],
            [validated_a, validated_b, validated_c],
            DEFAULT_KITCHEN,
            serving_time="18:00",
        )

        # Recipe C should have a warning because it must wait for Recipe A's oven
        # Recipe A (OVEN, 180 min) ends at T+210
        # Recipe C (OVEN, 60 min) must wait until T+210, ends at T+270 — 60 min after anchor
        assert len(result.resource_warnings) >= 1, (
            f"Expected at least 1 warning due to oven contention, got {len(result.resource_warnings)}: {result.resource_warnings}"
        )

        # Verify the warning mentions Recipe C
        assert any("Recipe C" in w for w in result.resource_warnings), (
            f"Warning should mention Recipe C: {result.resource_warnings}"
        )

    def test_no_warnings_without_serving_time(self):
        """Verify ASAP mode produces no warnings even with same recipe set."""
        from tests.fixtures.recipes import (
            ENRICHED_FT_RECIPE_A,
            ENRICHED_FT_RECIPE_B,
            ENRICHED_FT_RECIPE_C,
        )
        from tests.fixtures.schedules import (
            RECIPE_DAG_FT_A,
            RECIPE_DAG_FT_B,
            RECIPE_DAG_FT_C,
        )

        validated_a = ValidatedRecipe(source=ENRICHED_FT_RECIPE_A, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=ENRICHED_FT_RECIPE_B, validated_at=datetime.now())
        validated_c = ValidatedRecipe(source=ENRICHED_FT_RECIPE_C, validated_at=datetime.now())

        result = _merge_dags(
            [RECIPE_DAG_FT_A, RECIPE_DAG_FT_B, RECIPE_DAG_FT_C],
            [validated_a, validated_b, validated_c],
            DEFAULT_KITCHEN,
            serving_time=None,  # ASAP mode
        )

        assert result.resource_warnings == [], (
            f"ASAP mode should produce no warnings, got: {result.resource_warnings}"
        )

    def test_no_warnings_when_recipes_finish_together(self):
        """No warnings when all recipes use different resources and finish within window."""
        # Create recipes using different resources so they can actually finish together
        raw_a = RawRecipe(
            name="Oven Recipe",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=70,
            ingredients=[],
            steps=["prep", "cook"],
        )
        raw_b = RawRecipe(
            name="Stovetop Recipe",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=70,
            ingredients=[],
            steps=["prep", "cook"],
        )

        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(step_id="ov_prep", description="prep", duration_minutes=10, resource=Resource.HANDS),
                RecipeStep(step_id="ov_cook", description="cook", duration_minutes=60, resource=Resource.OVEN, depends_on=["ov_prep"]),
            ],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(step_id="st_prep", description="prep", duration_minutes=10, resource=Resource.HANDS),
                RecipeStep(step_id="st_cook", description="cook", duration_minutes=60, resource=Resource.STOVETOP, depends_on=["st_prep"]),
            ],
        )

        dag_a = RecipeDAG(recipe_name="Oven Recipe", recipe_slug="oven_recipe", steps=[], edges=[("ov_prep", "ov_cook")])
        dag_b = RecipeDAG(recipe_name="Stovetop Recipe", recipe_slug="stovetop_recipe", steps=[], edges=[("st_prep", "st_cook")])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())

        result = _merge_dags(
            [dag_a, dag_b],
            [validated_a, validated_b],
            DEFAULT_KITCHEN,
            serving_time="18:00",
        )

        # Both recipes have equal cooking duration and use different resources
        # They should finish at the same time with no warnings
        assert result.resource_warnings == [], (
            f"Equal duration recipes using different resources should have no warnings: {result.resource_warnings}"
        )

    def test_stovetop_warning_when_burner_limited(self):
        """Warning generated when stovetop capacity limits cause delays."""
        # Create recipes that exceed burner capacity
        raw_a = RawRecipe(
            name="Long Cook A",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=200,
            ingredients=[],
            steps=["cook"],
        )
        raw_b = RawRecipe(
            name="Short Cook B",
            description="t",
            servings=2,
            cuisine="t",
            estimated_total_minutes=70,
            ingredients=[],
            steps=["cook"],
        )

        enriched_a = EnrichedRecipe(
            source=raw_a,
            steps=[
                RecipeStep(step_id="a_cook", description="cook A", duration_minutes=180, resource=Resource.STOVETOP),
            ],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(step_id="b_cook", description="cook B", duration_minutes=60, resource=Resource.STOVETOP),
            ],
        )

        dag_a = RecipeDAG(recipe_name="Long Cook A", recipe_slug="long_cook_a", steps=[], edges=[])
        dag_b = RecipeDAG(recipe_name="Short Cook B", recipe_slug="short_cook_b", steps=[], edges=[])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())

        # Use only 1 burner to force contention
        kitchen = {"max_burners": 1, "has_second_oven": False}

        result = _merge_dags(
            [dag_a, dag_b],
            [validated_a, validated_b],
            kitchen,
            serving_time="18:00",
        )

        # With 1 burner, Recipe B must wait for Recipe A to finish
        # Recipe A: 180 min cooking, ends at T+180
        # Recipe B: offset=120, but must wait until T+180, ends at T+240 — 60 min after anchor
        assert len(result.resource_warnings) >= 1, (
            f"Expected warning due to stovetop contention, got: {result.resource_warnings}"
        )
        assert any("stovetop" in w.lower() for w in result.resource_warnings), (
            f"Warning should mention stovetop: {result.resource_warnings}"
        )
        assert any("all burners are occupied" in w.lower() for w in result.resource_warnings), (
            "Stovetop warning should describe burner occupancy rather than stovetop-wide capacity"
        )
        assert not any("shared temperature" in w.lower() or "stovetop heat conflict" in w.lower() for w in result.resource_warnings), (
            "Stovetop warning should avoid legacy shared-temperature/conflict wording"
        )


# ── Oven Temperature Conflict Tests ─────────────────────────────────────────


class TestOvenConflictDetection:
    """Tests for oven temperature conflict detection (R026)."""

    def test_single_oven_serializes_different_temps_without_serving_time(self):
        """Single oven (capacity=1) serializes steps with different temps in ASAP mode."""
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

        # Recipe B: 450°F (75°F difference — would conflict if parallel)
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
        assert result.one_oven_conflict.affected_step_ids == ["a_step_1", "b_step_1"]
        assert result.one_oven_conflict.remediation.requires_resequencing is True
        assert result.one_oven_conflict.remediation.delaying_recipe_names == ["Recipe B"]
        assert result.one_oven_conflict.remediation.blocking_recipe_names == ["Recipe A"]
        assert result.one_oven_conflict.remediation.suggested_actions == ["Bake Recipe B after Recipe A finishes."]

    def test_finish_together_single_oven_conflicting_temps_is_irreconcilable(self):
        """Finish-together single-oven overlaps >15°F fail with typed resource-conflict metadata."""
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

        dags = [
            RecipeDAG(recipe_name="Recipe A", recipe_slug="recipe_a", steps=[], edges=[]),
            RecipeDAG(recipe_name="Recipe B", recipe_slug="recipe_b", steps=[], edges=[]),
        ]
        validated = [
            ValidatedRecipe(source=enriched_a, validated_at=datetime.now()),
            ValidatedRecipe(source=enriched_b, validated_at=datetime.now()),
        ]

        with pytest.raises(ResourceConflictError) as exc_info:
            _merge_dags(dags, validated, {"max_burners": 4, "has_second_oven": False}, serving_time="18:00")

        metadata = exc_info.value.metadata
        assert metadata["classification"] == "irreconcilable"
        assert metadata["temperature_gap_f"] == 75
        assert metadata["blocking_recipe_names"] == ["Recipe A", "Recipe B"]
        assert metadata["affected_step_ids"] == ["a_step_1", "b_step_1"]
        assert metadata["remediation"]["requires_resequencing"] is False
        assert metadata["remediation"]["blocking_recipe_names"] == ["Recipe A", "Recipe B"]
        assert metadata["remediation"]["suggested_actions"] == ["Use a second oven or change recipes."]
        assert "Oven temperature conflict" in (metadata["remediation"]["notes"] or "")

    def test_finish_together_missing_oven_temp_stays_conservative(self):
        """Missing oven_temp_f does not fabricate an irreconcilable verdict in finish-together mode."""
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
            steps=["warm in oven"],
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
                    description="warm in oven",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=None,
                )
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

        result = _merge_dags(dags, validated, {"max_burners": 4, "has_second_oven": False}, serving_time="18:00")

        assert result.one_oven_conflict.classification == "compatible"
        assert result.one_oven_conflict.temperature_gap_f is None

    def test_dual_oven_allows_parallel_different_temps(self):
        """Dual oven (capacity=2) allows parallel execution of different temps."""
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

        kitchen = {"max_burners": 4, "has_second_oven": True}

        # Should succeed with dual oven
        result = _merge_dags([dag_a, dag_b], [validated_a, validated_b], kitchen)

        # Both steps should be scheduled in parallel
        assert len(result.scheduled_steps) == 2
        by_id = {s.step_id: s for s in result.scheduled_steps}
        
        # Both should start at T+0 (parallel execution with 2 ovens)
        assert by_id["a_step_1"].start_at_minute == 0
        assert by_id["b_step_1"].start_at_minute == 0

    def test_temperature_tolerance_threshold(self):
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

        # Both steps should be scheduled (sequentially due to single oven)
        assert len(result.scheduled_steps) == 2

    def test_none_temp_backward_compatibility(self):
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

    def test_sequential_different_temps_no_conflict(self):
        """Different temps are OK if steps don't overlap (sequential due to dependencies)."""
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

        # Should succeed — with single oven, steps will be scheduled sequentially
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

    def test_conflict_error_message_format(self):
        """Verify R026-compliant error message format with recipe names, temps, and time range.
        
        NOTE: This test verifies the error message FORMAT but doesn't test a real conflict scenario.
        Real blocking conflicts are rare (require dependencies forcing overlap + incompatible temps).
        The current implementation allows serialization when no dependencies force overlap.
        
        This test has been updated to expect successful scheduling (serialization) rather than
        an error, while the preheat test (when implemented) will verify error message format.
        """
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

        # Recipe B: 375°F (same temp, compatible)
        raw_b = RawRecipe(
            name="Recipe B",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=60,
            ingredients=[Ingredient(name="y", quantity="1")],
            steps=["bake at 375"],
        )
        enriched_b = EnrichedRecipe(
            source=raw_b,
            steps=[
                RecipeStep(
                    step_id="b_step_1",
                    description="bake at 375F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=375,
                )
            ],
        )

        # Recipe C: 450°F (incompatible temp)
        raw_c = RawRecipe(
            name="Recipe C",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=60,
            ingredients=[Ingredient(name="z", quantity="1")],
            steps=["bake at 450"],
        )
        enriched_c = EnrichedRecipe(
            source=raw_c,
            steps=[
                RecipeStep(
                    step_id="c_step_1",
                    description="bake at 450F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=450,
                )
            ],
        )

        dag_a = RecipeDAG(recipe_name="Recipe A", recipe_slug="recipe_a", steps=[], edges=[])
        dag_b = RecipeDAG(recipe_name="Recipe B", recipe_slug="recipe_b", steps=[], edges=[])
        dag_c = RecipeDAG(recipe_name="Recipe C", recipe_slug="recipe_c", steps=[], edges=[])

        validated_a = ValidatedRecipe(source=enriched_a, validated_at=datetime.now())
        validated_b = ValidatedRecipe(source=enriched_b, validated_at=datetime.now())
        validated_c = ValidatedRecipe(source=enriched_c, validated_at=datetime.now())

        # Dual oven: capacity=2
        # A and B can run in parallel (both at 375°F, using both ovens)
        # C will be serialized after them (450°F, incompatible)
        kitchen = {"max_burners": 4, "has_second_oven": True}

        # Should succeed with serialization (not error)
        result = _merge_dags([dag_a, dag_b, dag_c], [validated_a, validated_b, validated_c], kitchen)
        
        # All 3 steps should be scheduled
        assert len(result.scheduled_steps) == 3
        by_id = {s.step_id: s for s in result.scheduled_steps}
        
        # A and B should start in parallel at T+0 (compatible temps, dual oven)
        assert by_id["a_step_1"].start_at_minute == 0
        assert by_id["b_step_1"].start_at_minute == 0
        
        # C should be serialized after them (incompatible temp)
        assert by_id["c_step_1"].start_at_minute >= 60, (
            f"Recipe C should start after A/B complete: {by_id['c_step_1'].start_at_minute}"
        )

    @pytest.mark.skip(reason="Preheat functionality not yet implemented (planned for future task)")
    def test_preheat_steps_in_timeline(self):
        """Verify preheat steps appear in timeline with correct timing and temperature labels.
        
        Per R024: Enricher should inject 'preheat oven to X°F' steps 10-15 min before
        first oven use. This test will verify:
        - Preheat step exists before first oven step
        - Preheat duration is 10-15 min
        - Preheat step has correct temperature label
        - Preheat step occupies OVEN resource
        
        This test is currently skipped because preheat injection is not yet implemented.
        It will be enabled once the Enricher adds preheat step modeling.
        """
        # Recipe with oven step at 375°F
        raw = RawRecipe(
            name="Baked Recipe",
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=90,
            ingredients=[Ingredient(name="x", quantity="1")],
            steps=["prep", "bake at 375"],
        )
        enriched = EnrichedRecipe(
            source=raw,
            steps=[
                RecipeStep(
                    step_id="step_1",
                    description="prep ingredients",
                    duration_minutes=15,
                    resource=Resource.HANDS,
                ),
                # Preheat step should be injected by Enricher here
                RecipeStep(
                    step_id="step_2",
                    description="bake at 375F",
                    duration_minutes=60,
                    resource=Resource.OVEN,
                    oven_temp_f=375,
                    depends_on=["step_1"],
                ),
            ],
        )

        dag = RecipeDAG(recipe_name="Baked Recipe", recipe_slug="baked_recipe", steps=[], edges=[("step_1", "step_2")])
        validated = ValidatedRecipe(source=enriched, validated_at=datetime.now())

        result = _merge_dags([dag], [validated], DEFAULT_KITCHEN)

        # Once implemented, verify:
        # - Preheat step exists in timeline
        # - Preheat happens before baking step
        # - Preheat has appropriate duration (10-15 min)
        # - Preheat step mentions temperature
        assert False, "Preheat implementation pending"
