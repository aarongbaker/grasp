"""
tests/test_authored_recipe_models.py
Focused contract tests for the native authored-recipe domain seam.
"""

import pytest
from pydantic import ValidationError

from app.models.authored_recipe import (
    AuthoredRecipeCreate,
    AuthoredRecipeDependency,
    AuthoredRecipeStep,
    build_authored_step_id,
)
from tests.fixtures.recipes import AUTHORED_BRAISED_CHICKEN


def _authored_payload() -> dict:
    return AUTHORED_BRAISED_CHICKEN.model_dump(mode="python")



def test_authored_recipe_accepts_scheduling_and_service_metadata() -> None:
    authored = AuthoredRecipeCreate.model_validate(_authored_payload())

    assert authored.yield_info.unit == "plates"
    assert authored.storage is not None
    assert authored.storage.method == "Refrigerated in braising liquid"
    assert authored.hold is not None
    assert authored.hold.max_duration == "20 minutes"
    assert authored.reheat is not None
    assert authored.reheat.target == "165F in the thickest part"
    assert authored.make_ahead_guidance == (
        "Complete the braise the day before, chill overnight, and reheat in the liquid."
    )
    assert authored.steps[0].target_internal_temperature_f == 155
    assert authored.steps[1].yield_contribution == "Forms the braising liquor and onion garnish."
    assert authored.steps[2].until_condition == "The leg joint yields easily when nudged."



def test_compile_projection_is_deterministic_and_scheduler_compatible() -> None:
    authored = AuthoredRecipeCreate.model_validate(_authored_payload())

    raw_recipe = authored.compile_raw_recipe()
    compiled_steps = authored.compile_recipe_steps()

    assert raw_recipe.name == authored.title
    assert raw_recipe.servings == 4
    assert raw_recipe.estimated_total_minutes == 72
    assert raw_recipe.ingredients[0].name == "chicken leg quarters"
    assert [step.step_id for step in compiled_steps] == [
        "braised_chicken_with_saffron_onions_step_1",
        "braised_chicken_with_saffron_onions_step_2",
        "braised_chicken_with_saffron_onions_step_3",
    ]
    assert compiled_steps[1].depends_on == ["braised_chicken_with_saffron_onions_step_1"]
    assert compiled_steps[2].depends_on == ["braised_chicken_with_saffron_onions_step_2"]
    assert compiled_steps[2].prep_ahead_window == "up to 2 days ahead"
    assert "Target temp: 155F" in compiled_steps[0].description
    assert "Yield: Forms the braising liquor and onion garnish." in compiled_steps[1].description
    assert "Until: The leg joint yields easily when nudged." in compiled_steps[2].description



def test_authored_recipe_rejects_dangling_dependencies() -> None:
    payload = _authored_payload()
    payload["steps"][1]["dependencies"] = [
        AuthoredRecipeDependency(step_id="missing_step_999").model_dump(mode="python")
    ]

    with pytest.raises(ValidationError) as excinfo:
        AuthoredRecipeCreate.model_validate(payload)

    assert "depends on 'missing_step_999' which does not exist" in str(excinfo.value)



def test_authored_recipe_rejects_contradictory_timing_shapes() -> None:
    payload = _authored_payload()
    payload["steps"][0]["duration_max"] = 10

    with pytest.raises(ValidationError) as excinfo:
        AuthoredRecipeCreate.model_validate(payload)

    assert "duration_max (10) must be >= duration_minutes (12)" in str(excinfo.value)


@pytest.mark.parametrize(
    ("step_update", "expected_message"),
    [
        (
            {"can_be_done_ahead": True, "prep_ahead_window": None},
            "prep_ahead_window is required when can_be_done_ahead is true",
        ),
        (
            {"can_be_done_ahead": False, "prep_ahead_window": "up to 1 day"},
            "prep_ahead_window/prep_ahead_notes require can_be_done_ahead=true",
        ),
    ],
)
def test_authored_step_validation_surfaces_prep_ahead_contract_errors(step_update: dict, expected_message: str) -> None:
    payload = _authored_payload()
    payload["steps"][2].update(step_update)

    with pytest.raises(ValidationError) as excinfo:
        AuthoredRecipeCreate.model_validate(payload)

    assert expected_message in str(excinfo.value)



def test_step_id_helper_matches_existing_scheduler_shape() -> None:
    assert build_authored_step_id("Braised Chicken with Saffron Onions", 2) == (
        "braised_chicken_with_saffron_onions_step_2"
    )



def test_required_equipment_rejects_duplicates() -> None:
    with pytest.raises(ValidationError) as excinfo:
        AuthoredRecipeStep(
            title="Toast spices",
            instruction="Warm the spices gently until fragrant.",
            duration_minutes=4,
            resource="hands",
            required_equipment=["spice grinder", "spice grinder"],
        )

    assert "required_equipment must not contain duplicates" in str(excinfo.value)
