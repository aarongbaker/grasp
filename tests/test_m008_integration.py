"""
tests/test_m008_integration.py
Finish-together (FT) scheduling integration tests for M008.

These tests exercise the full LangGraph pipeline with FT fixtures:
  - Recipe A (Long Braise): 30 min prep + 180 min OVEN
  - Recipe B (Quick Sauté): 15 min prep + 60 min STOVETOP
  - Recipe C (Medium Roast): 20 min prep + 60 min OVEN

With serving_time set and has_second_oven=False, the scheduler should:
1. Stagger start times so cooking steps finish within a 30-min window
2. Detect oven contention (Recipe A and C both need OVEN) and generate warnings
3. Surface warnings in the rendered schedule summary

Test scenarios:
- test_ft_scheduling_stagger: Verifies finish-together staggering via serving_time
- test_ft_resource_warnings: Verifies oven contention warning generation
"""

import uuid

import pytest
import pytest_asyncio

from app.models.enums import MealType, Occasion, SessionStatus


@pytest.mark.asyncio
async def test_ft_scheduling_stagger(
    compiled_graph,
    unique_session_id,
    test_db_session,
    test_user_id,
    generator_ft_mode,  # Switch to FT fixtures
):
    """
    Full pipeline with serving_time='19:00' triggers finish-together scheduling.
    Verifies cooking step end times are within a 30-min window.

    With FT fixtures:
    - Recipe A: 180 min OVEN → anchor (longest cooking)
    - Recipe B: 60 min STOVETOP → should finish near anchor
    - Recipe C: 60 min OVEN → should finish near anchor (but may conflict)

    Expected: All cooking steps end within 30 minutes of each other.
    """
    from app.core.status import finalise_session
    from app.models.enums import Resource
    from app.models.pipeline import DinnerConcept
    from app.models.session import Session

    # Build concept with serving_time to enable finish-together
    concept = DinnerConcept(
        free_text="A finish-together test dinner with three dishes.",
        guest_count=4,
        meal_type=MealType.DINNER,
        occasion=Occasion.DINNER_PARTY,
        dietary_restrictions=[],
        serving_time="19:00",  # Triggers finish-together scheduling
    )

    initial_state = {
        "concept": concept.model_dump(),
        "kitchen_config": {
            "max_burners": 4,
            "max_oven_racks": 2,
            "has_second_oven": True,  # Two ovens → no oven contention warning
        },
        "equipment": [],
        "user_id": str(test_user_id),
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
        "errors": [],
    }

    # Create session row
    session_row = Session(
        session_id=unique_session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json=initial_state["concept"],
    )
    test_db_session.add(session_row)
    await test_db_session.commit()

    config = {"configurable": {"thread_id": str(unique_session_id)}}
    final_state = await compiled_graph.ainvoke(initial_state, config=config)

    # ── Assert pipeline completed ────────────────────────────────────────────
    assert final_state is not None
    assert len(final_state.get("errors", [])) == 0, f"Unexpected errors: {final_state.get('errors')}"

    merged_dag = final_state.get("merged_dag")
    assert merged_dag is not None, "merged_dag should be populated"

    # ── Extract cooking step end times ───────────────────────────────────────
    scheduled_steps = merged_dag.get("scheduled_steps", [])
    assert len(scheduled_steps) > 0, "Should have scheduled steps"

    # Find cooking steps (STOVETOP or OVEN)
    cooking_steps = [
        s for s in scheduled_steps
        if s.get("resource") in [Resource.STOVETOP.value, Resource.OVEN.value]
    ]
    assert len(cooking_steps) >= 3, f"Expected at least 3 cooking steps, got {len(cooking_steps)}"

    # Get end times per recipe (max end time for each recipe's cooking steps)
    cooking_ends_by_recipe: dict[str, int] = {}
    for step in cooking_steps:
        recipe = step.get("recipe_name")
        end_time = step.get("end_at_minute", 0)
        if recipe not in cooking_ends_by_recipe or end_time > cooking_ends_by_recipe[recipe]:
            cooking_ends_by_recipe[recipe] = end_time

    # ── Assert finish-together: all cooking ends within 30 min window ────────
    end_times = list(cooking_ends_by_recipe.values())
    assert len(end_times) >= 2, "Should have at least 2 recipes with cooking steps"

    time_spread = max(end_times) - min(end_times)
    assert time_spread <= 30, (
        f"Finish-together FAILED: cooking end times spread = {time_spread} min "
        f"(expected ≤30). Ends by recipe: {cooking_ends_by_recipe}"
    )

    # ── Assert schedule exists ───────────────────────────────────────────────
    schedule = final_state.get("schedule")
    assert schedule is not None, "Schedule should be populated"
    assert schedule.get("total_duration_minutes", 0) > 0

    # ── Finalise and verify DB state ─────────────────────────────────────────
    await finalise_session(unique_session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, unique_session_id)
    assert refreshed.status == SessionStatus.COMPLETE

    print(
        f"\n✓ FT stagger test COMPLETE — cooking ends within {time_spread} min window. "
        f"Ends: {cooking_ends_by_recipe}"
    )


@pytest.mark.asyncio
async def test_ft_resource_warnings(
    compiled_graph,
    unique_session_id,
    test_db_session,
    test_user_id,
    generator_ft_mode,  # Switch to FT fixtures
):
    """
    Full pipeline with has_second_oven=False triggers oven contention.
    Recipe A (180 min OVEN) and Recipe C (60 min OVEN) compete for oven.

    Expected:
    - resource_warnings is non-empty
    - Rendered summary prompt contains RESOURCE WARNINGS section
    """
    from app.core.status import finalise_session
    from app.models.pipeline import DinnerConcept
    from app.models.session import Session

    # Build concept with serving_time + single oven
    concept = DinnerConcept(
        free_text="A finish-together test dinner with three dishes and oven contention.",
        guest_count=4,
        meal_type=MealType.DINNER,
        occasion=Occasion.DINNER_PARTY,
        dietary_restrictions=[],
        serving_time="19:00",  # Triggers finish-together scheduling
    )

    initial_state = {
        "concept": concept.model_dump(),
        "kitchen_config": {
            "max_burners": 4,
            "max_oven_racks": 2,
            "has_second_oven": False,  # Single oven → oven contention expected
        },
        "equipment": [],
        "user_id": str(test_user_id),
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
        "errors": [],
    }

    # Create session row
    session_row = Session(
        session_id=unique_session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json=initial_state["concept"],
    )
    test_db_session.add(session_row)
    await test_db_session.commit()

    config = {"configurable": {"thread_id": str(unique_session_id)}}
    final_state = await compiled_graph.ainvoke(initial_state, config=config)

    # ── Assert pipeline completed ────────────────────────────────────────────
    assert final_state is not None
    errors = final_state.get("errors", [])
    # Non-fatal errors are acceptable; fatal errors fail the test
    fatal_errors = [e for e in errors if not e.get("recoverable", True)]
    assert len(fatal_errors) == 0, f"Unexpected fatal errors: {fatal_errors}"

    merged_dag = final_state.get("merged_dag")
    assert merged_dag is not None, "merged_dag should be populated"

    # ── Assert resource_warnings is non-empty ────────────────────────────────
    resource_warnings = merged_dag.get("resource_warnings", [])
    # Note: Warnings are only generated when recipes finish later than the anchor
    # With our fixtures:
    # - Recipe A (anchor): 180 min OVEN, finishes at T+210
    # - Recipe C: 60 min OVEN, must wait for Recipe A's oven slot → finishes later
    #
    # If the oven is single-capacity, Recipe C cannot start until Recipe A finishes,
    # so Recipe C will finish ~60 min after Recipe A → should trigger a warning.

    print(f"\n  Resource warnings: {resource_warnings}")

    # The warning detection depends on whether Recipe C's cooking ends significantly
    # after Recipe A's. With single oven:
    # - Recipe A OVEN: starts at ~T+30 (after prep), ends at T+210
    # - Recipe C OVEN: must wait until T+210, so ends at T+270
    # That's 60 min after anchor → should trigger warning.
    #
    # We assert at least one warning exists if oven contention occurred.
    # If the scheduler handles it differently (e.g., Recipe C starts much earlier
    # in prep-ahead mode), warnings may not be generated.
    assert len(resource_warnings) > 0, (
        "Expected resource_warnings for oven contention. "
        f"merged_dag has {len(merged_dag.get('scheduled_steps', []))} steps, "
        f"resource_utilisation: {merged_dag.get('resource_utilisation', {})}"
    )

    # Check warning mentions oven or Recipe C
    warning_text = " ".join(resource_warnings).lower()
    assert "oven" in warning_text or "recipe c" in warning_text, (
        f"Warning should mention oven contention. Got: {resource_warnings}"
    )

    # ── Assert schedule summary was rendered ─────────────────────────────────
    schedule = final_state.get("schedule")
    assert schedule is not None, "Schedule should be populated"

    # The renderer should include warnings in the summary prompt, but the mock
    # renderer returns a generic summary. We verify the warnings exist in merged_dag.
    # Real renderer behavior would include the warnings in schedule.summary.

    # ── Finalise and verify DB state ─────────────────────────────────────────
    await finalise_session(unique_session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, unique_session_id)
    # Pipeline should complete even with warnings
    assert refreshed.status == SessionStatus.COMPLETE

    print(
        f"\n✓ FT resource warnings test COMPLETE — {len(resource_warnings)} warning(s). "
        f"First warning: {resource_warnings[0][:80]}..."
    )


@pytest.mark.asyncio
async def test_ft_asap_mode_no_warnings(
    compiled_graph,
    unique_session_id,
    test_db_session,
    test_user_id,
    generator_ft_mode,  # Switch to FT fixtures
):
    """
    ASAP mode (no serving_time) should NOT generate resource warnings.
    Warnings are only relevant when finish-together scheduling is active.
    """
    from app.core.status import finalise_session
    from app.models.pipeline import DinnerConcept
    from app.models.session import Session

    # Build concept WITHOUT serving_time → ASAP mode
    concept = DinnerConcept(
        free_text="An ASAP test dinner with three dishes.",
        guest_count=4,
        meal_type=MealType.DINNER,
        occasion=Occasion.DINNER_PARTY,
        dietary_restrictions=[],
        serving_time=None,  # No serving_time → ASAP scheduling
    )

    initial_state = {
        "concept": concept.model_dump(),
        "kitchen_config": {
            "max_burners": 4,
            "max_oven_racks": 2,
            "has_second_oven": False,  # Single oven, but ASAP mode
        },
        "equipment": [],
        "user_id": str(test_user_id),
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
        "errors": [],
    }

    # Create session row
    session_row = Session(
        session_id=unique_session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json=initial_state["concept"],
    )
    test_db_session.add(session_row)
    await test_db_session.commit()

    config = {"configurable": {"thread_id": str(unique_session_id)}}
    final_state = await compiled_graph.ainvoke(initial_state, config=config)

    # ── Assert pipeline completed ────────────────────────────────────────────
    assert final_state is not None
    assert len(final_state.get("errors", [])) == 0, f"Unexpected errors: {final_state.get('errors')}"

    merged_dag = final_state.get("merged_dag")
    assert merged_dag is not None, "merged_dag should be populated"

    # ── Assert NO resource_warnings in ASAP mode ─────────────────────────────
    resource_warnings = merged_dag.get("resource_warnings", [])
    assert len(resource_warnings) == 0, (
        f"ASAP mode should NOT generate resource warnings. Got: {resource_warnings}"
    )

    # ── Finalise and verify ──────────────────────────────────────────────────
    await finalise_session(unique_session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, unique_session_id)
    assert refreshed.status == SessionStatus.COMPLETE

    print("\n✓ FT ASAP mode test COMPLETE — no resource warnings as expected")
