import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.core.status import finalise_session
from app.graph.graph import build_grasp_graph
from app.models.enums import ErrorType, MealType, Occasion, Resource, SessionStatus
from app.models.pipeline import DinnerConcept, GenerationRetryReason, build_initial_pipeline_state
from app.models.recipe import EnrichedRecipe, Ingredient, RawRecipe, RecipeStep
from app.models.scheduling import MergedDAG, NaturalLanguageSchedule, OneOvenConflictSummary, RecipeDAG, ScheduledStep
from app.models.session import Session
from tests.fixtures.recipes import ENRICHED_FT_RECIPE_A, ENRICHED_FT_RECIPE_B, ENRICHED_FT_RECIPE_C
from tests.fixtures.schedules import RECIPE_DAG_FT_A, RECIPE_DAG_FT_B, RECIPE_DAG_FT_C


KITCHEN_CONFIG = {
    "max_burners": 4,
    "max_oven_racks": 2,
    "has_second_oven": False,
}


@pytest.fixture
def dinner_concept() -> DinnerConcept:
    return DinnerConcept(
        free_text="A French dinner party with a braise, a quick sauté, and a roast.",
        guest_count=4,
        meal_type=MealType.DINNER,
        occasion=Occasion.DINNER_PARTY,
        dietary_restrictions=[],
        serving_time="18:00",
    )


@pytest.fixture
def initial_state(dinner_concept: DinnerConcept) -> dict:
    return build_initial_pipeline_state(
        concept=dinner_concept,
        user_id="user-123",
        rag_owner_key="rag-owner-123",
        kitchen_config=KITCHEN_CONFIG,
        equipment=[],
    )


def _make_retryable_conflict_error(*, attempt: int) -> dict:
    detail = "Recipe A Long Braise at 300°F conflicts with Recipe C Medium Roast at 450°F from 16:00 to 17:00."
    return {
        "node_name": "dag_merger",
        "error_type": ErrorType.RESOURCE_CONFLICT.value,
        "recoverable": False,
        "message": detail,
        "metadata": {
            "classification": "irreconcilable",
            "tolerance_f": 15,
            "has_second_oven": False,
            "temperature_gap_f": 150,
            "blocking_recipe_names": ["Recipe A Long Braise", "Recipe C Medium Roast"],
            "affected_step_ids": ["ft_recipe_a_cook", "ft_recipe_c_cook"],
            "remediation": {
                "requires_resequencing": False,
                "suggested_actions": ["Use a second oven or change recipes."],
                "delaying_recipe_names": [],
                "blocking_recipe_names": ["Recipe A Long Braise", "Recipe C Medium Roast"],
                "notes": detail,
            },
            "detail": detail,
            "attempt": attempt,
        },
    }


def _make_non_retryable_conflict_error() -> dict:
    return {
        "node_name": "dag_merger",
        "error_type": ErrorType.RESOURCE_CONFLICT.value,
        "recoverable": False,
        "message": "Single-oven schedule remains feasible by staging incompatible oven temperatures into separate windows.",
        "metadata": {
            "classification": "resequence_required",
            "tolerance_f": 15,
            "has_second_oven": False,
            "temperature_gap_f": 75,
            "blocking_recipe_names": ["Recipe A Long Braise", "Recipe C Medium Roast"],
            "affected_step_ids": ["ft_recipe_a_cook", "ft_recipe_c_cook"],
            "remediation": {
                "requires_resequencing": True,
                "suggested_actions": ["Bake Recipe C Medium Roast after Recipe A Long Braise finishes."],
                "delaying_recipe_names": ["Recipe C Medium Roast"],
                "blocking_recipe_names": ["Recipe A Long Braise"],
                "notes": "Single-oven schedule remains feasible by staging incompatible oven temperatures into separate windows.",
            },
        },
    }


def _raw_recipe(name: str) -> RawRecipe:
    return RawRecipe(
        name=name,
        description=f"Fixture recipe for {name}.",
        servings=4,
        cuisine="French",
        estimated_total_minutes=60,
        ingredients=[Ingredient(name="ingredient", quantity="1")],
        steps=["Prep ingredients.", "Cook.", "Serve immediately."],
    )


SUCCESS_RECIPE_NAMES = ["Recipe A Long Braise", "Recipe B Quick Saute", "Recipe C Stove Finish"]
FAILURE_RECIPE_NAMES = ["Recipe A Long Braise", "Recipe B Quick Saute", "Recipe C Medium Roast"]

SUCCESS_MERGED_DAG = MergedDAG(
    scheduled_steps=[
        ScheduledStep(
            step_id="ft_recipe_a_cook",
            recipe_name="Recipe A Long Braise",
            description="Braise for 3 hours in oven",
            resource=Resource.OVEN,
            duration_minutes=180,
            start_at_minute=0,
            end_at_minute=180,
        ),
        ScheduledStep(
            step_id="ft_recipe_b_cook",
            recipe_name="Recipe B Quick Saute",
            description="Sauté on stovetop for 1 hour",
            resource=Resource.STOVETOP,
            duration_minutes=60,
            start_at_minute=0,
            end_at_minute=60,
        ),
        ScheduledStep(
            step_id="ft_recipe_c_stove_cook",
            recipe_name="Recipe C Stove Finish",
            description="Cook on stovetop for 1 hour",
            resource=Resource.STOVETOP,
            duration_minutes=60,
            start_at_minute=60,
            end_at_minute=120,
        ),
    ],
    total_duration_minutes=180,
    total_duration_minutes_max=180,
    active_time_minutes=120,
    one_oven_conflict=OneOvenConflictSummary(
        classification="compatible",
        tolerance_f=15,
        has_second_oven=False,
    ),
)

SUCCESS_ENRICHED = [
    ENRICHED_FT_RECIPE_A,
    ENRICHED_FT_RECIPE_B,
    EnrichedRecipe(
        source=_raw_recipe("Recipe C Stove Finish"),
        steps=[
            RecipeStep(
                step_id="ft_recipe_c_stove_prep",
                description="Prepare ingredients for stove finish",
                duration_minutes=20,
                depends_on=[],
                resource=Resource.HANDS,
            ),
            RecipeStep(
                step_id="ft_recipe_c_stove_cook",
                description="Cook on stovetop for 1 hour",
                duration_minutes=60,
                depends_on=["ft_recipe_c_stove_prep"],
                resource=Resource.STOVETOP,
            ),
        ],
        chef_notes="Stovetop replacement avoids the conflicting roast.",
        techniques_used=["saute"],
    ),
]

SUCCESS_DAGS = [
    RECIPE_DAG_FT_A,
    RECIPE_DAG_FT_B,
    RecipeDAG(
        recipe_name="Recipe C Stove Finish",
        recipe_slug="recipe_c_stove_finish",
        steps=[],
        edges=[("ft_recipe_c_stove_prep", "ft_recipe_c_stove_cook")],
    ),
]

FAILURE_ENRICHED = [ENRICHED_FT_RECIPE_A, ENRICHED_FT_RECIPE_B, ENRICHED_FT_RECIPE_C]
FAILURE_DAGS = [RECIPE_DAG_FT_A, RECIPE_DAG_FT_B, RECIPE_DAG_FT_C]


def _build_generation_result(*, attempt: int, recipe_names: list[str], token_usage: dict, retry_reason: dict | None) -> dict:
    return {
        "raw_recipes": [_raw_recipe(name).model_dump(mode="json") for name in recipe_names],
        "token_usage": [token_usage],
        "generation_history": [
            {
                "attempt": attempt,
                "trigger": "auto_repair" if retry_reason is not None else "initial",
                "recipe_names": recipe_names,
                "retry_reason": retry_reason,
            }
        ],
        "generation_retry_reason": None,
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
    }


def _compile_graph_with_controlled_generator(*, generator_side_effect, dag_merger_side_effect, renderer_summary="Recovered schedule"):
    async def _enricher_side_effect(state):
        names = [recipe["name"] for recipe in state.get("raw_recipes", [])]
        if names == SUCCESS_RECIPE_NAMES:
            return {"enriched_recipes": [recipe.model_dump(mode="json") for recipe in SUCCESS_ENRICHED]}
        return {"enriched_recipes": [recipe.model_dump(mode="json") for recipe in FAILURE_ENRICHED]}

    async def _validator_side_effect(state):
        return {"validated_recipes": state.get("enriched_recipes", [])}

    async def _dag_builder_side_effect(state):
        names = [recipe["source"]["name"] for recipe in state.get("validated_recipes", [])]
        if names == SUCCESS_RECIPE_NAMES:
            return {"recipe_dags": [dag.model_dump(mode="json") for dag in SUCCESS_DAGS]}
        return {"recipe_dags": [dag.model_dump(mode="json") for dag in FAILURE_DAGS]}

    renderer = AsyncMock(
        return_value={
            "schedule": NaturalLanguageSchedule(
                timeline=[],
                prep_ahead_entries=[],
                total_duration_minutes=SUCCESS_MERGED_DAG.total_duration_minutes,
                total_duration_minutes_max=SUCCESS_MERGED_DAG.total_duration_minutes_max,
                active_time_minutes=SUCCESS_MERGED_DAG.active_time_minutes,
                summary=renderer_summary,
                error_summary=None,
                one_oven_conflict=SUCCESS_MERGED_DAG.one_oven_conflict,
            ).model_dump(mode="json"),
            "token_usage": [{"node_name": "schedule_renderer", "input_tokens": 5, "output_tokens": 7}],
        }
    )

    with (
        patch("app.graph.graph.recipe_generator_node", AsyncMock(side_effect=generator_side_effect)) as generator,
        patch("app.graph.graph.rag_enricher_node", AsyncMock(side_effect=_enricher_side_effect)) as enricher,
        patch("app.graph.graph.validator_node", AsyncMock(side_effect=_validator_side_effect)) as validator,
        patch("app.graph.graph.dag_builder_node", AsyncMock(side_effect=_dag_builder_side_effect)) as dag_builder,
        patch("app.graph.graph.dag_merger_node", AsyncMock(side_effect=dag_merger_side_effect)) as dag_merger,
        patch("app.graph.graph.schedule_renderer_node", renderer),
    ):
        graph = build_grasp_graph(MemorySaver())

    return graph, {
        "generator": generator,
        "enricher": enricher,
        "validator": validator,
        "dag_builder": dag_builder,
        "dag_merger": dag_merger,
        "renderer": renderer,
    }


@pytest.mark.asyncio
async def test_full_graph_auto_repair_succeeds_on_second_attempt(initial_state):
    retry_reason = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary.model_validate(_make_retryable_conflict_error(attempt=1)["metadata"]),
        detail=_make_retryable_conflict_error(attempt=1)["metadata"]["detail"],
        attempt=1,
    ).model_dump(mode="json")
    graph, calls = _compile_graph_with_controlled_generator(
        generator_side_effect=[
            _build_generation_result(
                attempt=1,
                recipe_names=FAILURE_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20},
                retry_reason=None,
            ),
            _build_generation_result(
                attempt=2,
                recipe_names=SUCCESS_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 11, "output_tokens": 21},
                retry_reason=retry_reason,
            ),
        ],
        dag_merger_side_effect=[
            {"errors": [_make_retryable_conflict_error(attempt=1)]},
            {"merged_dag": SUCCESS_MERGED_DAG.model_dump(mode="json")},
        ],
        renderer_summary="Recovered schedule",
    )

    config = {"configurable": {"thread_id": f"m024-success-{uuid.uuid4()}"}, "recursion_limit": 20}
    final_state = await graph.ainvoke(initial_state, config=config)

    assert calls["generator"].await_count == 2
    assert calls["dag_merger"].await_count == 1
    assert calls["renderer"].await_count == 0
    assert final_state["generation_attempt"] == 2
    assert final_state["generation_retry_reason"] is None
    assert [entry["attempt"] for entry in final_state["generation_history"]] == [2]
    assert final_state["generation_history"][0]["trigger"] == "auto_repair"
    assert final_state["generation_history"][0]["recipe_names"] == SUCCESS_RECIPE_NAMES
    assert final_state["raw_recipes"] == [_raw_recipe(name).model_dump(mode="json") for name in SUCCESS_RECIPE_NAMES]
    assert "schedule" not in final_state or final_state["schedule"] is None
    assert final_state["errors"] == [_make_retryable_conflict_error(attempt=1)]
    assert final_state["token_usage"] == [
        {"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20},
        {"node_name": "recipe_generator", "input_tokens": 11, "output_tokens": 21},
    ]


@pytest.mark.asyncio
async def test_full_graph_auto_repair_exhausts_and_stays_failed(initial_state):
    retry_reason_1 = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary.model_validate(_make_retryable_conflict_error(attempt=1)["metadata"]),
        detail=_make_retryable_conflict_error(attempt=1)["metadata"]["detail"],
        attempt=1,
    ).model_dump(mode="json")
    retry_reason_2 = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary.model_validate(_make_retryable_conflict_error(attempt=2)["metadata"]),
        detail=_make_retryable_conflict_error(attempt=2)["metadata"]["detail"],
        attempt=2,
    ).model_dump(mode="json")
    graph, calls = _compile_graph_with_controlled_generator(
        generator_side_effect=[
            _build_generation_result(
                attempt=1,
                recipe_names=FAILURE_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20},
                retry_reason=None,
            ),
            _build_generation_result(
                attempt=2,
                recipe_names=FAILURE_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 11, "output_tokens": 21},
                retry_reason=retry_reason_1,
            ),
            _build_generation_result(
                attempt=3,
                recipe_names=FAILURE_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 12, "output_tokens": 22},
                retry_reason=retry_reason_2,
            ),
        ],
        dag_merger_side_effect=[
            {"errors": [_make_retryable_conflict_error(attempt=1)]},
            {"errors": [_make_retryable_conflict_error(attempt=2)]},
            {"errors": [_make_retryable_conflict_error(attempt=3)]},
        ],
        renderer_summary="Should not render",
    )

    config = {"configurable": {"thread_id": f"m024-exhausted-{uuid.uuid4()}"}, "recursion_limit": 20}
    final_state = await graph.ainvoke(initial_state, config=config)

    assert calls["generator"].await_count == 2
    assert calls["dag_merger"].await_count == 1
    assert calls["renderer"].await_count == 0
    assert final_state["generation_attempt"] == 2
    assert final_state["generation_retry_reason"] is None
    assert [entry["attempt"] for entry in final_state["generation_history"]] == [2]
    assert [entry["trigger"] for entry in final_state["generation_history"]] == ["auto_repair"]
    assert final_state["errors"] == [_make_retryable_conflict_error(attempt=1)]
    assert "schedule" not in final_state or final_state["schedule"] is None
    assert final_state["token_usage"] == [
        {"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20},
        {"node_name": "recipe_generator", "input_tokens": 11, "output_tokens": 21},
    ]


@pytest.mark.asyncio
async def test_full_graph_non_retryable_conflict_fails_immediately(initial_state):
    graph, calls = _compile_graph_with_controlled_generator(
        generator_side_effect=[
            _build_generation_result(
                attempt=1,
                recipe_names=FAILURE_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20},
                retry_reason=None,
            )
        ],
        dag_merger_side_effect=[{"errors": [_make_non_retryable_conflict_error()]}],
        renderer_summary="Should not render",
    )

    config = {"configurable": {"thread_id": f"m024-nonretry-{uuid.uuid4()}"}, "recursion_limit": 20}
    final_state = await graph.ainvoke(initial_state, config=config)

    assert calls["generator"].await_count == 1
    assert calls["dag_merger"].await_count == 1
    assert calls["renderer"].await_count == 0
    assert final_state["generation_attempt"] == 1
    assert final_state["generation_retry_reason"] is None
    assert final_state["generation_history"] == [
        {
            "attempt": 1,
            "trigger": "initial",
            "recipe_names": FAILURE_RECIPE_NAMES,
            "retry_reason": None,
        }
    ]
    assert final_state["errors"] == [_make_non_retryable_conflict_error()]
    assert final_state["token_usage"] == [{"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20}]


@pytest.mark.asyncio
async def test_finalise_session_persists_repaired_success_with_only_final_artifacts(test_db_session, test_user_id, initial_state):
    retry_reason = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary.model_validate(_make_retryable_conflict_error(attempt=1)["metadata"]),
        detail=_make_retryable_conflict_error(attempt=1)["metadata"]["detail"],
        attempt=1,
    ).model_dump(mode="json")
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json=initial_state["concept"],
    )
    test_db_session.add(session)
    await test_db_session.commit()

    graph, _calls = _compile_graph_with_controlled_generator(
        generator_side_effect=[
            _build_generation_result(
                attempt=1,
                recipe_names=FAILURE_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20},
                retry_reason=None,
            ),
            _build_generation_result(
                attempt=2,
                recipe_names=SUCCESS_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 11, "output_tokens": 21},
                retry_reason=retry_reason,
            ),
        ],
        dag_merger_side_effect=[
            {"errors": [_make_retryable_conflict_error(attempt=1)]},
            {"merged_dag": SUCCESS_MERGED_DAG.model_dump(mode="json")},
        ],
        renderer_summary="Recovered schedule",
    )

    config = {"configurable": {"thread_id": f"m024-repaired-success-{uuid.uuid4()}"}, "recursion_limit": 20}
    final_state = await graph.ainvoke(initial_state, config=config)
    final_state["schedule"] = NaturalLanguageSchedule(
        timeline=[],
        prep_ahead_entries=[],
        total_duration_minutes=SUCCESS_MERGED_DAG.total_duration_minutes,
        total_duration_minutes_max=SUCCESS_MERGED_DAG.total_duration_minutes_max,
        active_time_minutes=SUCCESS_MERGED_DAG.active_time_minutes,
        summary="Recovered schedule",
        error_summary=None,
        one_oven_conflict=SUCCESS_MERGED_DAG.one_oven_conflict,
    ).model_dump(mode="json")
    final_state["validated_recipes"] = [recipe.model_dump(mode="json") for recipe in SUCCESS_ENRICHED]

    await finalise_session(session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, session_id)
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert refreshed.schedule_summary == "Recovered schedule"
    assert refreshed.total_duration_minutes == SUCCESS_MERGED_DAG.total_duration_minutes
    assert refreshed.result_schedule is not None
    assert refreshed.result_schedule["summary"] == "Recovered schedule"
    assert refreshed.result_schedule["one_oven_conflict"]["classification"] == "compatible"
    assert refreshed.result_recipes is not None
    assert [recipe["source"]["name"] for recipe in refreshed.result_recipes] == SUCCESS_RECIPE_NAMES
    assert all(name != "Recipe C Medium Roast" for name in [recipe["source"]["name"] for recipe in refreshed.result_recipes])
    assert refreshed.error_summary is not None
    assert "dag_merger:" in refreshed.error_summary
    assert refreshed.token_usage == {
        "total_input_tokens": 21,
        "total_output_tokens": 41,
        "per_node": [
            {"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20},
            {"node_name": "recipe_generator", "input_tokens": 11, "output_tokens": 21},
        ],
    }


@pytest.mark.asyncio
async def test_finalise_session_persists_failed_exhausted_retry_token_usage(test_db_session, test_user_id, initial_state):
    retry_reason_1 = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary.model_validate(_make_retryable_conflict_error(attempt=1)["metadata"]),
        detail=_make_retryable_conflict_error(attempt=1)["metadata"]["detail"],
        attempt=1,
    ).model_dump(mode="json")
    retry_reason_2 = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=OneOvenConflictSummary.model_validate(_make_retryable_conflict_error(attempt=2)["metadata"]),
        detail=_make_retryable_conflict_error(attempt=2)["metadata"]["detail"],
        attempt=2,
    ).model_dump(mode="json")
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json=initial_state["concept"],
    )
    test_db_session.add(session)
    await test_db_session.commit()

    graph, _calls = _compile_graph_with_controlled_generator(
        generator_side_effect=[
            _build_generation_result(
                attempt=1,
                recipe_names=FAILURE_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20},
                retry_reason=None,
            ),
            _build_generation_result(
                attempt=2,
                recipe_names=FAILURE_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 11, "output_tokens": 21},
                retry_reason=retry_reason_1,
            ),
            _build_generation_result(
                attempt=3,
                recipe_names=FAILURE_RECIPE_NAMES,
                token_usage={"node_name": "recipe_generator", "input_tokens": 12, "output_tokens": 22},
                retry_reason=retry_reason_2,
            ),
        ],
        dag_merger_side_effect=[
            {"errors": [_make_retryable_conflict_error(attempt=1)]},
            {"errors": [_make_retryable_conflict_error(attempt=2)]},
            {"errors": [_make_retryable_conflict_error(attempt=3)]},
        ],
    )

    config = {"configurable": {"thread_id": f"m024-finalise-{uuid.uuid4()}"}, "recursion_limit": 20}
    final_state = await graph.ainvoke(initial_state, config=config)
    await finalise_session(session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, session_id)
    assert refreshed is not None
    assert refreshed.status == SessionStatus.FAILED
    assert refreshed.result_schedule is None
    assert refreshed.result_recipes is None
    assert refreshed.error_summary is not None
    assert "dag_merger:" in refreshed.error_summary
    assert refreshed.token_usage == {
        "total_input_tokens": 33,
        "total_output_tokens": 63,
        "per_node": [
            {"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20},
            {"node_name": "recipe_generator", "input_tokens": 11, "output_tokens": 21},
            {"node_name": "recipe_generator", "input_tokens": 12, "output_tokens": 22},
        ],
    }


@pytest.mark.asyncio
async def test_finalise_session_persists_exhausted_repair_metadata_without_losing_resource_conflict_semantics(
    test_db_session,
    test_user_id,
    initial_state,
):
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json=initial_state["concept"],
    )
    test_db_session.add(session)
    await test_db_session.commit()

    exhausted_error = _make_retryable_conflict_error(attempt=3)
    exhausted_error["metadata"] = {
        **exhausted_error["metadata"],
        "automatic_repair_attempted": True,
        "automatic_repair_exhausted": True,
        "automatic_repair_attempt_count": 3,
        "generation_history": [
            {
                "attempt": 1,
                "trigger": "initial",
                "recipe_names": FAILURE_RECIPE_NAMES,
                "retry_reason": None,
            },
            {
                "attempt": 2,
                "trigger": "auto_repair",
                "recipe_names": FAILURE_RECIPE_NAMES,
                "retry_reason": {
                    "node_name": "dag_merger",
                    "error_type": ErrorType.RESOURCE_CONFLICT.value,
                    "detail": _make_retryable_conflict_error(attempt=1)["message"],
                    "attempt": 1,
                },
            },
            {
                "attempt": 3,
                "trigger": "auto_repair",
                "recipe_names": FAILURE_RECIPE_NAMES,
                "retry_reason": {
                    "node_name": "dag_merger",
                    "error_type": ErrorType.RESOURCE_CONFLICT.value,
                    "detail": _make_retryable_conflict_error(attempt=2)["message"],
                    "attempt": 2,
                },
            },
        ],
    }
    final_state = {
        **initial_state,
        "errors": [exhausted_error],
        "schedule": None,
        "validated_recipes": [],
        "token_usage": [
            {"node_name": "recipe_generator", "input_tokens": 10, "output_tokens": 20},
            {"node_name": "recipe_generator", "input_tokens": 11, "output_tokens": 21},
            {"node_name": "recipe_generator", "input_tokens": 12, "output_tokens": 22},
        ],
    }

    await finalise_session(session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, session_id)
    assert refreshed is not None
    assert refreshed.status == SessionStatus.FAILED
    assert refreshed.result_schedule is None
    assert refreshed.result_recipes is None
    assert refreshed.error_summary is not None
    assert "dag_merger:" in refreshed.error_summary

    persisted = (
        await test_db_session.execute(select(Session.error_summary, Session.token_usage).where(Session.session_id == session_id))
    ).one()
    assert persisted.token_usage["total_input_tokens"] == 33
    assert exhausted_error["error_type"] == ErrorType.RESOURCE_CONFLICT.value
    assert exhausted_error["metadata"]["automatic_repair_attempted"] is True
    assert exhausted_error["metadata"]["automatic_repair_exhausted"] is True
    assert exhausted_error["metadata"]["automatic_repair_attempt_count"] == 3
