from datetime import datetime

import pytest

from app.graph.nodes.dag_merger import ResourceConflictError, _merge_dags
from app.models.enums import Resource
from app.models.recipe import EnrichedRecipe, RawRecipe, RecipeStep, ValidatedRecipe
from app.models.scheduling import RecipeDAG


def _make_validated_recipe(
    name: str,
    slug: str,
    step_id: str,
    *,
    resource: Resource,
    duration_minutes: int = 15,
    required_equipment: list[str] | None = None,
) -> tuple[RecipeDAG, ValidatedRecipe]:
    raw = RawRecipe(
        name=name,
        description="test",
        servings=2,
        cuisine="test",
        estimated_total_minutes=duration_minutes,
        ingredients=[],
        steps=["do the thing"],
    )
    enriched = EnrichedRecipe(
        source=raw,
        steps=[
            RecipeStep(
                step_id=step_id,
                description=f"{name} step",
                duration_minutes=duration_minutes,
                resource=resource,
                required_equipment=required_equipment or [],
            )
        ],
    )
    dag = RecipeDAG(recipe_name=name, recipe_slug=slug, steps=[], edges=[])
    return dag, ValidatedRecipe(source=enriched, validated_at=datetime.now())


def test_zero_burners_does_not_schedule_stovetop_steps():
    dag, validated = _make_validated_recipe(
        "Pan Sauce",
        "pan_sauce",
        "pan_sauce_step_1",
        resource=Resource.STOVETOP,
    )

    with pytest.raises(ResourceConflictError, match="no burner slots configured"):
        _merge_dags([dag], [validated], {"max_burners": 0, "has_second_oven": False})


def test_missing_kitchen_config_uses_defaults_without_crashing():
    dag, validated = _make_validated_recipe(
        "Quick Saute",
        "quick_saute",
        "quick_saute_step_1",
        resource=Resource.STOVETOP,
    )

    result = _merge_dags([dag], [validated], {})

    assert result.total_duration_minutes == 15
    assert len(result.scheduled_steps) == 1
    assert result.scheduled_steps[0].burner_id == "burner_1"


def test_malformed_burner_descriptors_do_not_raise_unhandled_exception():
    dag, validated = _make_validated_recipe(
        "Shallow Fry",
        "shallow_fry",
        "shallow_fry_step_1",
        resource=Resource.STOVETOP,
    )

    result = _merge_dags(
        [dag],
        [validated],
        {
            "max_burners": 2,
            "burners": [{"position": "front_left", "size": "large"}],
        },
    )

    assert len(result.scheduled_steps) == 1
    assert result.scheduled_steps[0].burner_id == "burner_1"
