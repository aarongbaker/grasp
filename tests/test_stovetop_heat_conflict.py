"""
Focused stovetop burner-allocation regression tests for Phase 6 DAG merger.

These tests document the S02 burner-local placement policy:
- mixed stovetop heats can run in parallel on separate suitable burners
- burner heat continuity is a local preference signal, not a stovetop-wide blocker
- coarse burner-size suitability keeps large-pan work off undersized burners when alternatives exist
- a single suitable burner still serializes overlapping stovetop work on that burner identity
- when no suitable burner is free, the scheduler waits for the next suitable release boundary
- missing stovetop heat metadata remains deterministic and does not invent false conflicts

The suite intentionally does not model speculative burner capabilities beyond the existing
small/medium/large descriptor classes.
"""

from datetime import datetime

from app.graph.nodes.dag_merger import _BurnerSlot, _IntervalIndex, _StepInfo, _find_stovetop_slot, _merge_dags
from app.models.enums import Resource
from app.models.recipe import EnrichedRecipe, Ingredient, RawRecipe, RecipeStep, ValidatedRecipe
from app.models.scheduling import RecipeDAG


class TestStovetopHeatConflict:
    def _make_recipe(
        self,
        *,
        name: str,
        slug: str,
        step_id: str,
        resource: Resource,
        duration: int,
        depends_on: list[str] | None = None,
        oven_temp_f: int | None = None,
        description: str | None = None,
    ) -> tuple[RecipeDAG, ValidatedRecipe]:
        raw = RawRecipe(
            name=name,
            description="test",
            servings=2,
            cuisine="test",
            estimated_total_minutes=duration,
            ingredients=[Ingredient(name="item", quantity="1")],
            steps=["cook"],
        )
        enriched = EnrichedRecipe(
            source=raw,
            steps=[
                RecipeStep(
                    step_id=step_id,
                    description=description or f"{name} step",
                    duration_minutes=duration,
                    resource=resource,
                    depends_on=depends_on or [],
                    oven_temp_f=oven_temp_f,
                )
            ],
        )
        dag = RecipeDAG(recipe_name=name, recipe_slug=slug, steps=[], edges=[])
        validated = ValidatedRecipe(source=enriched, validated_at=datetime.now())
        return dag, validated

    def test_mixed_heats_can_run_in_parallel_on_separate_burners(self):
        high_heat_dag, high_heat_val = self._make_recipe(
            name="High Heat Sear",
            slug="high_heat_sear",
            step_id="high_heat_step",
            resource=Resource.STOVETOP,
            duration=20,
            description="Sear over high heat_f=450",
        )
        low_heat_dag, low_heat_val = self._make_recipe(
            name="Low Heat Simmer",
            slug="low_heat_simmer",
            step_id="low_heat_step",
            resource=Resource.STOVETOP,
            duration=20,
            description="Simmer gently heat_f=250",
        )

        kitchen = {
            "max_burners": 2,
            "burners": [
                {"burner_id": "burner_a", "size": "large", "label": "Burner A"},
                {"burner_id": "burner_b", "size": "medium", "label": "Burner B"},
            ],
        }

        result = _merge_dags(
            [high_heat_dag, low_heat_dag],
            [high_heat_val, low_heat_val],
            kitchen,
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["high_heat_step"].start_at_minute == 0
        assert by_id["low_heat_step"].start_at_minute == 0
        assert by_id["high_heat_step"].burner_id != by_id["low_heat_step"].burner_id

    def test_descriptor_backed_burners_preserve_stable_identity_and_context(self):
        dag_a, val_a = self._make_recipe(
            name="Large Sauce",
            slug="large_sauce",
            step_id="large_step",
            resource=Resource.STOVETOP,
            duration=25,
        )
        dag_b, val_b = self._make_recipe(
            name="Medium Soup",
            slug="medium_soup",
            step_id="medium_step",
            resource=Resource.STOVETOP,
            duration=25,
        )
        dag_c, val_c = self._make_recipe(
            name="Second Medium",
            slug="second_medium",
            step_id="second_medium_step",
            resource=Resource.STOVETOP,
            duration=25,
        )
        dag_d, val_d = self._make_recipe(
            name="Small Simmer",
            slug="small_simmer",
            step_id="small_step",
            resource=Resource.STOVETOP,
            duration=25,
        )

        kitchen = {
            "max_burners": 4,
            "burners": [
                {
                    "burner_id": "front_left_large",
                    "position": "front_left",
                    "size": "large",
                    "label": "Front Left Large",
                },
                {
                    "burner_id": "front_right_medium",
                    "position": "front_right",
                    "size": "medium",
                    "label": "Front Right Medium",
                },
                {
                    "burner_id": "rear_left_medium",
                    "position": "rear_left",
                    "size": "medium",
                    "label": "Rear Left Medium",
                },
                {
                    "burner_id": "rear_right_small",
                    "position": "rear_right",
                    "size": "small",
                    "label": "Rear Right Small",
                },
            ],
        }

        result = _merge_dags(
            [dag_a, dag_b, dag_c, dag_d],
            [val_a, val_b, val_c, val_d],
            kitchen,
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert all(step.start_at_minute == 0 for step in result.scheduled_steps)
        assert by_id["large_step"].burner_id == "front_left_large"
        assert by_id["large_step"].burner_position == "front_left"
        assert by_id["large_step"].burner_size == "large"
        assert by_id["large_step"].burner_label == "Front Left Large"
        assert by_id["large_step"].burner is not None
        assert by_id["large_step"].burner.burner_id == "front_left_large"

        assert by_id["medium_step"].burner_id == "front_right_medium"
        assert by_id["medium_step"].burner_size == "medium"
        assert by_id["second_medium_step"].burner_id == "rear_left_medium"
        assert by_id["second_medium_step"].burner_size == "medium"
        assert by_id["small_step"].burner_id == "rear_right_small"
        assert by_id["small_step"].burner_size == "small"

    def test_max_burners_only_config_synthesizes_stable_burner_ids(self):
        dag_a, val_a = self._make_recipe(
            name="Recipe A",
            slug="recipe_a",
            step_id="a_step",
            resource=Resource.STOVETOP,
            duration=20,
        )
        dag_b, val_b = self._make_recipe(
            name="Recipe B",
            slug="recipe_b",
            step_id="b_step",
            resource=Resource.STOVETOP,
            duration=20,
        )

        result = _merge_dags(
            [dag_a, dag_b],
            [val_a, val_b],
            {"max_burners": 2, "has_second_oven": False},
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["a_step"].start_at_minute == 0
        assert by_id["b_step"].start_at_minute == 0
        assert by_id["a_step"].burner_id == "burner_1"
        assert by_id["a_step"].burner_label == "Burner 1"
        assert by_id["a_step"].burner_position is None
        assert by_id["a_step"].burner_size is None
        assert by_id["b_step"].burner_id == "burner_2"
        assert by_id["b_step"].burner_label == "Burner 2"

    def test_heat_continuity_prefers_matching_recent_burner_when_suitable_burners_are_free(self):
        step = _StepInfo(
            step_id="target_step",
            recipe_name="Target",
            recipe_slug="target",
            description="Warm sauce on stovetop heat_f=300",
            resource=Resource.STOVETOP,
            duration_minutes=15,
            stovetop_heat_f=300,
        )
        burner_slots = [
            _BurnerSlot(burner_id="large_a", size="large", label="Large A"),
            _BurnerSlot(burner_id="large_b", size="large", label="Large B"),
        ]
        burner_intervals = {slot.burner_id: _IntervalIndex() for slot in burner_slots}
        burner_history = {
            "large_a": [(10, 450)],
            "large_b": [(10, 275)],
        }

        start, burner = _find_stovetop_slot(
            step,
            10,
            burner_slots,
            burner_intervals,
            burner_history,
        )

        assert start == 10
        assert burner.burner_id == "large_b"

    def test_size_suitability_prefers_large_burner_for_large_pan_signal(self):
        large_pan_dag, large_pan_val = self._make_recipe(
            name="Large Pan Sear",
            slug="large_pan_sear",
            step_id="large_pan_step",
            resource=Resource.STOVETOP,
            duration=20,
            description="Sear steaks in a large pan over high-heat sear heat_f=450",
        )
        simmer_dag, simmer_val = self._make_recipe(
            name="Gentle Simmer",
            slug="gentle_simmer",
            step_id="simmer_step",
            resource=Resource.STOVETOP,
            duration=20,
            description="Keep sauce at a gentle simmer heat_f=275",
        )

        kitchen = {
            "max_burners": 2,
            "burners": [
                {"burner_id": "small_front", "size": "small", "label": "Small Front"},
                {"burner_id": "large_rear", "size": "large", "label": "Large Rear"},
            ],
        }

        result = _merge_dags(
            [large_pan_dag, simmer_dag],
            [large_pan_val, simmer_val],
            kitchen,
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["large_pan_step"].burner_id == "large_rear"
        assert by_id["large_pan_step"].burner_size == "large"
        assert by_id["simmer_step"].burner_id == "small_front"
        assert by_id["simmer_step"].burner_size == "small"

    def test_single_burner_serializes_overlapping_stovetop_steps_on_same_identity(self):
        dag_a, val_a = self._make_recipe(
            name="Recipe A",
            slug="recipe_a",
            step_id="a_step",
            resource=Resource.STOVETOP,
            duration=30,
            description="Recipe A step heat_f=450",
        )
        dag_b, val_b = self._make_recipe(
            name="Recipe B",
            slug="recipe_b",
            step_id="b_step",
            resource=Resource.STOVETOP,
            duration=15,
            description="Recipe B step heat_f=275",
        )

        result = _merge_dags(
            [dag_a, dag_b],
            [val_a, val_b],
            {
                "max_burners": 1,
                "burners": [
                    {
                        "burner_id": "solo_burner",
                        "position": "front_center",
                        "size": "large",
                        "label": "Solo Burner",
                    }
                ],
            },
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["a_step"].start_at_minute == 0
        assert by_id["a_step"].end_at_minute == 30
        assert by_id["b_step"].start_at_minute == 30
        assert by_id["b_step"].end_at_minute == 45
        assert by_id["a_step"].burner_id == "solo_burner"
        assert by_id["b_step"].burner_id == "solo_burner"
        assert by_id["b_step"].burner_label == "Solo Burner"

    def test_single_suitable_burner_serializes_even_when_other_unsuitable_burner_is_free(self):
        large_pan_dag, large_pan_val = self._make_recipe(
            name="Large Pan Sear",
            slug="large_pan_sear",
            step_id="large_pan_step",
            resource=Resource.STOVETOP,
            duration=30,
            description="Sear in a large pan heat_f=450",
        )
        second_large_pan_dag, second_large_pan_val = self._make_recipe(
            name="Second Large Pan",
            slug="second_large_pan",
            step_id="second_large_pan_step",
            resource=Resource.STOVETOP,
            duration=15,
            description="Finish in a large pan heat_f=300",
        )

        kitchen = {
            "max_burners": 2,
            "burners": [
                {"burner_id": "small_front", "size": "small", "label": "Small Front"},
                {"burner_id": "large_rear", "size": "large", "label": "Large Rear"},
            ],
        }

        result = _merge_dags(
            [large_pan_dag, second_large_pan_dag],
            [large_pan_val, second_large_pan_val],
            kitchen,
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["large_pan_step"].start_at_minute == 0
        assert by_id["large_pan_step"].burner_id == "large_rear"
        assert by_id["second_large_pan_step"].start_at_minute == 30
        assert by_id["second_large_pan_step"].burner_id == "large_rear"

    def test_saturated_suitable_burners_wait_for_next_release_boundary(self):
        large_a_dag, large_a_val = self._make_recipe(
            name="Large Burner A",
            slug="large_burner_a",
            step_id="large_a_step",
            resource=Resource.STOVETOP,
            duration=30,
            description="Sear in a large pan heat_f=450",
        )
        large_b_dag, large_b_val = self._make_recipe(
            name="Large Burner B",
            slug="large_burner_b",
            step_id="large_b_step",
            resource=Resource.STOVETOP,
            duration=20,
            description="Cook in a large pan heat_f=425",
        )
        waiting_dag, waiting_val = self._make_recipe(
            name="Waiting Large Pan",
            slug="waiting_large_pan",
            step_id="waiting_step",
            resource=Resource.STOVETOP,
            duration=10,
            description="Warm in a large pan heat_f=300",
        )

        kitchen = {
            "max_burners": 3,
            "burners": [
                {"burner_id": "large_a", "size": "large", "label": "Large A"},
                {"burner_id": "large_b", "size": "large", "label": "Large B"},
                {"burner_id": "small_c", "size": "small", "label": "Small C"},
            ],
        }

        result = _merge_dags(
            [large_a_dag, large_b_dag, waiting_dag],
            [large_a_val, large_b_val, waiting_val],
            kitchen,
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["large_a_step"].start_at_minute == 0
        assert by_id["large_b_step"].start_at_minute == 0
        assert by_id["waiting_step"].start_at_minute == 20
        assert by_id["waiting_step"].burner_id == "large_b"

    def test_none_heat_remains_parallel_safe_without_false_conflicts(self):
        no_heat_dag, no_heat_val = self._make_recipe(
            name="No Heat Metadata",
            slug="no_heat_metadata",
            step_id="no_heat_step",
            resource=Resource.STOVETOP,
            duration=20,
            description="Keep the sauce moving on the stove",
        )
        explicit_heat_dag, explicit_heat_val = self._make_recipe(
            name="Explicit Heat",
            slug="explicit_heat",
            step_id="explicit_heat_step",
            resource=Resource.STOVETOP,
            duration=20,
            description="Cook quickly over direct heat_f=450",
        )

        kitchen = {
            "max_burners": 2,
            "burners": [
                {"burner_id": "burner_a", "size": "medium", "label": "Burner A"},
                {"burner_id": "burner_b", "size": "medium", "label": "Burner B"},
            ],
        }

        result = _merge_dags(
            [no_heat_dag, explicit_heat_dag],
            [no_heat_val, explicit_heat_val],
            kitchen,
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["no_heat_step"].start_at_minute == 0
        assert by_id["explicit_heat_step"].start_at_minute == 0
        assert by_id["no_heat_step"].burner_id != by_id["explicit_heat_step"].burner_id

    def test_mixed_oven_and_stovetop_keeps_oven_behavior_while_assigning_burners(self):
        oven_a_dag, oven_a_val = self._make_recipe(
            name="Roast A",
            slug="roast_a",
            step_id="oven_a",
            resource=Resource.OVEN,
            duration=40,
            oven_temp_f=375,
        )
        oven_b_dag, oven_b_val = self._make_recipe(
            name="Roast B",
            slug="roast_b",
            step_id="oven_b",
            resource=Resource.OVEN,
            duration=20,
            oven_temp_f=450,
        )
        stovetop_dag, stovetop_val = self._make_recipe(
            name="Sauce",
            slug="sauce",
            step_id="stovetop_step",
            resource=Resource.STOVETOP,
            duration=15,
        )

        kitchen = {
            "max_burners": 2,
            "burners": [
                {"burner_id": "front_left_large", "position": "front_left", "size": "large", "label": "Front Left"},
                {"burner_id": "rear_right_small", "position": "rear_right", "size": "small", "label": "Rear Right"},
            ],
            "has_second_oven": False,
        }

        result = _merge_dags(
            [oven_a_dag, oven_b_dag, stovetop_dag],
            [oven_a_val, oven_b_val, stovetop_val],
            kitchen,
        )
        by_id = {step.step_id: step for step in result.scheduled_steps}

        assert by_id["oven_a"].start_at_minute == 0
        assert by_id["oven_a"].end_at_minute == 40
        assert by_id["oven_b"].start_at_minute == 40
        assert by_id["oven_b"].end_at_minute == 60

        assert by_id["stovetop_step"].start_at_minute == 0
        assert by_id["stovetop_step"].end_at_minute == 15
        assert by_id["stovetop_step"].burner_id == "front_left_large"
        assert by_id["stovetop_step"].burner_position == "front_left"
        assert by_id["stovetop_step"].burner_size == "large"
        assert by_id["stovetop_step"].burner_label == "Front Left"
