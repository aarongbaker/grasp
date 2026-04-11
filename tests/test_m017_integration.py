import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.sessions import router as sessions_router
from app.core.auth import get_current_user
from app.db.session import get_session
from app.api.routes.catalog import load_catalog_runtime_seed_recipes
from app.models.authored_recipe import AuthoredRecipeCreate, AuthoredRecipeRecord, RecipeCookbookRecord
from app.models.enums import MealType, Occasion, SessionStatus
from app.models.pipeline import (
    DinnerConcept,
    PlannerCatalogCookbookReference,
    PlannerLibraryAuthoredRecipeAnchor,
    PlannerLibraryCookbookPlanningMode,
    PlannerLibraryCookbookTarget,
    build_session_initial_state,
)
from app.models.session import Session
from app.models.user import UserProfile
from tests.fixtures.recipes import AUTHORED_BRAISED_CHICKEN


def _create_results_app(*, db_session, current_user: UserProfile) -> FastAPI:
    app = FastAPI()
    app.include_router(sessions_router, prefix="/api/v1")

    async def _override_session():
        yield db_session

    async def _override_user():
        return current_user

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = _override_user
    return app


def _authored_recipe_payload(*, title: str, description: str, cuisine: str, cookbook_id: uuid.UUID | None = None) -> dict:
    payload = AUTHORED_BRAISED_CHICKEN.model_dump(mode="python")
    payload["title"] = title
    payload["description"] = description
    payload["cuisine"] = cuisine
    payload["cookbook_id"] = cookbook_id

    slug = title.lower().replace("&", "and")
    slug = "_".join(filter(None, [part.strip("_-") for part in slug.replace("-", " ").split()]))
    source_slug = AUTHORED_BRAISED_CHICKEN.title.lower().replace("&", "and")
    source_slug = "_".join(filter(None, [part.strip("_-") for part in source_slug.replace("-", " ").split()]))

    payload["steps"] = [
        {
            **step,
            "dependencies": [
                {
                    **dependency,
                    "step_id": dependency["step_id"].replace(source_slug, slug),
                }
                for dependency in step.get("dependencies", [])
            ],
        }
        for step in payload["steps"]
    ]
    return payload


async def _seed_authored_recipe(
    db_session,
    *,
    user_id: uuid.UUID,
    title: str,
    description: str,
    cuisine: str,
    cookbook_id: uuid.UUID | None = None,
) -> AuthoredRecipeRecord:
    payload = _authored_recipe_payload(
        title=title,
        description=description,
        cuisine=cuisine,
        cookbook_id=cookbook_id,
    )
    authored = AuthoredRecipeCreate.model_validate(
        {
            **payload,
            "user_id": user_id,
            "cookbook_id": cookbook_id,
        }
    )
    record = AuthoredRecipeRecord(
        user_id=user_id,
        cookbook_id=cookbook_id,
        title=authored.title,
        description=authored.description,
        cuisine=authored.cuisine,
        authored_payload=authored.model_dump(mode="json"),
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record


async def _run_persisted_session(
    *,
    compiled_graph,
    test_db_session,
    session_row: Session,
    user: UserProfile,
    authored_record_map: dict[uuid.UUID, AuthoredRecipeRecord] | None = None,
    cookbook_record_map: dict[uuid.UUID, list[AuthoredRecipeRecord]] | None = None,
) -> dict:
    concept, initial_state = build_session_initial_state(
        concept_payload=session_row.concept_json,
        user_id=str(user.user_id),
        rag_owner_key=user.rag_owner_key,
        kitchen_config={
            "max_burners": 4,
            "max_oven_racks": 2,
            "has_second_oven": False,
        },
        equipment=[],
    )
    assert concept.concept_source == session_row.concept_json["concept_source"]

    config = {"configurable": {"thread_id": str(session_row.session_id)}}

    authored_record_map = authored_record_map or {}
    cookbook_record_map = cookbook_record_map or {}

    async def _load_authored_recipe_record(selection):
        record = authored_record_map.get(selection.recipe_id)
        if record is None:
            raise ValueError(f"Selected authored recipe {selection.title!r} ({selection.recipe_id}) was not found")
        return record

    async def _load_cookbook_authored_recipe_records(target):
        return list(cookbook_record_map.get(target.cookbook_id, []))

    with (
        patch("app.graph.nodes.generator._load_authored_recipe_record", side_effect=_load_authored_recipe_record),
        patch(
            "app.graph.nodes.generator._load_cookbook_authored_recipe_records",
            side_effect=_load_cookbook_authored_recipe_records,
        ),
    ):
        return await compiled_graph.ainvoke(initial_state, config=config)


async def _get_results_payload(*, db_session, current_user: UserProfile, session_id: uuid.UUID) -> dict:
    app = _create_results_app(db_session=db_session, current_user=current_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get(f"/api/v1/sessions/{session_id}/results")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_planner_authored_anchor_session_persists_mixed_origin_results(
    compiled_graph,
    unique_session_id,
    test_db_session,
    test_user_id,
):
    from app.core.status import finalise_session

    user = await test_db_session.get(UserProfile, test_user_id)
    assert user is not None

    anchor_record = await _seed_authored_recipe(
        test_db_session,
        user_id=test_user_id,
        title="Braised Short Ribs",
        description="A long braise that anchors the meal.",
        cuisine="French-American",
    )

    concept = DinnerConcept(
        free_text="Plan a balanced dinner around my short ribs.",
        guest_count=4,
        meal_type=MealType.DINNER,
        occasion=Occasion.DINNER_PARTY,
        dietary_restrictions=[],
        concept_source="planner_authored_anchor",
        planner_authored_recipe_anchor=PlannerLibraryAuthoredRecipeAnchor(
            recipe_id=anchor_record.recipe_id,
            title=anchor_record.title,
        ),
    )

    session_row = Session(
        session_id=unique_session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json=concept.model_dump(mode="json"),
    )
    test_db_session.add(session_row)
    await test_db_session.commit()

    final_state = await _run_persisted_session(
        compiled_graph=compiled_graph,
        test_db_session=test_db_session,
        session_row=session_row,
        user=user,
        authored_record_map={anchor_record.recipe_id: anchor_record},
    )

    assert final_state["schedule"] is not None
    assert final_state["errors"] == []
    assert len(final_state["raw_recipes"]) >= 3

    raw_provenance_kinds = [recipe["provenance"]["kind"] for recipe in final_state["raw_recipes"]]
    assert raw_provenance_kinds[0] == "library_authored"
    assert all(kind == "generated" for kind in raw_provenance_kinds[1:])

    await finalise_session(unique_session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, unique_session_id)
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert refreshed.result_schedule is not None
    assert refreshed.result_recipes is not None
    assert refreshed.completed_at is not None
    assert refreshed.error_summary is None

    persisted_provenance_kinds = [
        recipe["source"]["source"]["provenance"]["kind"] for recipe in refreshed.result_recipes
    ]
    assert persisted_provenance_kinds[0] == "library_authored"
    assert all(kind == "generated" for kind in persisted_provenance_kinds[1:])
    assert refreshed.result_recipes[0]["source"]["source"]["provenance"] == {
        "kind": "library_authored",
        "source_label": anchor_record.title,
        "recipe_id": str(anchor_record.recipe_id),
        "cookbook_id": None,
    }

    results_payload = await _get_results_payload(
        db_session=test_db_session,
        current_user=user,
        session_id=unique_session_id,
    )
    assert results_payload["errors"] == []
    assert results_payload["schedule"]["total_duration_minutes"] == refreshed.total_duration_minutes
    assert results_payload["recipes"][0]["source"]["source"]["provenance"]["kind"] == "library_authored"
    assert all(
        recipe["source"]["source"]["provenance"]["kind"] == "generated"
        for recipe in results_payload["recipes"][1:]
    )
    assert results_payload["recipes"][0]["source"]["source"]["provenance"]["recipe_id"] == str(anchor_record.recipe_id)


@pytest.mark.asyncio
async def test_planner_catalog_cookbook_session_persists_catalog_seeded_results_without_private_cookbook_semantics(
    compiled_graph,
    unique_session_id,
    test_db_session,
    test_user_id,
):
    from app.core.status import finalise_session

    user = await test_db_session.get(UserProfile, test_user_id)
    assert user is not None

    concept = DinnerConcept(
        free_text="Plan dinner from the platform catalog foundations lane.",
        guest_count=4,
        meal_type=MealType.DINNER,
        occasion=Occasion.CASUAL,
        dietary_restrictions=[],
        concept_source="planner_catalog_cookbook",
        planner_catalog_cookbook=PlannerCatalogCookbookReference(
            catalog_cookbook_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            slug="weeknight-foundations",
            title="Weeknight Foundations",
            access_state="included",
            access_state_reason="Included with the base catalog",
        ),
    )

    session_row = Session(
        session_id=unique_session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json=concept.model_dump(mode="json"),
    )
    test_db_session.add(session_row)
    await test_db_session.commit()

    final_state = await _run_persisted_session(
        compiled_graph=compiled_graph,
        test_db_session=test_db_session,
        session_row=session_row,
        user=user,
    )

    assert final_state["schedule"] is not None
    assert final_state["errors"] == []
    assert len(final_state["raw_recipes"]) == 2
    assert [recipe["name"] for recipe in final_state["raw_recipes"]] == [
        "Skillet Chicken Piccata",
        "Tomato Braised Chickpeas",
    ]
    assert [recipe["provenance"]["kind"] for recipe in final_state["raw_recipes"]] == [
        "library_cookbook",
        "library_cookbook",
    ]
    assert all(
        recipe["provenance"]["source_label"] == "catalog:weeknight-foundations:Weeknight Foundations"
        for recipe in final_state["raw_recipes"]
    )
    assert all(recipe["provenance"]["cookbook_id"] == "11111111-1111-1111-1111-111111111111" for recipe in final_state["raw_recipes"])

    await finalise_session(unique_session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, unique_session_id)
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert refreshed.result_schedule is not None
    assert refreshed.result_recipes is not None

    persisted_provenance = [recipe["source"]["source"]["provenance"] for recipe in refreshed.result_recipes]
    assert [item["kind"] for item in persisted_provenance] == ["library_cookbook", "library_cookbook"]
    assert all(item["recipe_id"] is None for item in persisted_provenance)
    assert all(item["cookbook_id"] == "11111111-1111-1111-1111-111111111111" for item in persisted_provenance)
    assert all(
        item["source_label"] == "catalog:weeknight-foundations:Weeknight Foundations"
        for item in persisted_provenance
    )

    results_payload = await _get_results_payload(
        db_session=test_db_session,
        current_user=user,
        session_id=unique_session_id,
    )
    assert results_payload["errors"] == []
    assert [recipe["source"]["source"]["provenance"]["kind"] for recipe in results_payload["recipes"]] == [
        "library_cookbook",
        "library_cookbook",
    ]
    assert all(
        recipe["source"]["source"]["provenance"]["source_label"]
        == "catalog:weeknight-foundations:Weeknight Foundations"
        for recipe in results_payload["recipes"]
    )


@pytest.mark.asyncio
async def test_planner_cookbook_target_session_persists_cookbook_biased_results(
    compiled_graph,
    unique_session_id,
    test_db_session,
    test_user_id,
):
    from app.core.status import finalise_session

    user = await test_db_session.get(UserProfile, test_user_id)
    assert user is not None

    cookbook = RecipeCookbookRecord(
        user_id=test_user_id,
        name="Vegetarian Lunch",
        description="Market-led vegetarian planning shelf.",
    )
    test_db_session.add(cookbook)
    await test_db_session.commit()
    await test_db_session.refresh(cookbook)

    seeded_one = await _seed_authored_recipe(
        test_db_session,
        user_id=test_user_id,
        title="Pommes Puree",
        description="Silky potato purée.",
        cuisine="French",
        cookbook_id=cookbook.cookbook_id,
    )
    seeded_two = await _seed_authored_recipe(
        test_db_session,
        user_id=test_user_id,
        title="Chocolate Fondant",
        description="Molten chocolate puddings.",
        cuisine="French-British",
        cookbook_id=cookbook.cookbook_id,
    )

    concept = DinnerConcept(
        free_text="Vegetarian lunch from my vegetarian cookbook.",
        guest_count=4,
        meal_type=MealType.DINNER,
        occasion=Occasion.DINNER_PARTY,
        dietary_restrictions=["vegetarian"],
        concept_source="planner_cookbook_target",
        planner_cookbook_target=PlannerLibraryCookbookTarget(
            cookbook_id=cookbook.cookbook_id,
            name=cookbook.name,
            description=cookbook.description,
            mode=PlannerLibraryCookbookPlanningMode.COOKBOOK_BIASED,
        ),
    )

    session_row = Session(
        session_id=unique_session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json=concept.model_dump(mode="json"),
    )
    test_db_session.add(session_row)
    await test_db_session.commit()

    final_state = await _run_persisted_session(
        compiled_graph=compiled_graph,
        test_db_session=test_db_session,
        session_row=session_row,
        user=user,
        authored_record_map={
            seeded_one.recipe_id: seeded_one,
            seeded_two.recipe_id: seeded_two,
        },
        cookbook_record_map={cookbook.cookbook_id: [seeded_two, seeded_one]},
    )

    assert final_state["schedule"] is not None
    assert final_state["errors"] == []
    assert len(final_state["raw_recipes"]) >= 3

    raw_recipes = final_state["raw_recipes"]
    assert [recipe["name"] for recipe in raw_recipes[:2]] == [seeded_two.title, seeded_one.title]
    assert [recipe["provenance"]["kind"] for recipe in raw_recipes[:2]] == [
        "library_authored",
        "library_authored",
    ]
    assert all(recipe["provenance"]["kind"] == "generated" for recipe in raw_recipes[2:])
    assert [recipe["provenance"]["cookbook_id"] for recipe in raw_recipes[:2]] == [
        str(cookbook.cookbook_id),
        str(cookbook.cookbook_id),
    ]

    await finalise_session(unique_session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, unique_session_id)
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert refreshed.result_schedule is not None
    assert refreshed.result_recipes is not None
    assert refreshed.completed_at is not None

    persisted_recipes = refreshed.result_recipes
    persisted_provenance = [recipe["source"]["source"]["provenance"] for recipe in persisted_recipes]
    assert [item["kind"] for item in persisted_provenance[:2]] == [
        "library_authored",
        "library_authored",
    ]
    assert all(item["kind"] == "generated" for item in persisted_provenance[2:])
    assert persisted_provenance[0]["cookbook_id"] == str(cookbook.cookbook_id)
    assert persisted_provenance[1]["cookbook_id"] == str(cookbook.cookbook_id)
    assert {persisted_provenance[0]["recipe_id"], persisted_provenance[1]["recipe_id"]} == {
        str(seeded_one.recipe_id),
        str(seeded_two.recipe_id),
    }

    results_payload = await _get_results_payload(
        db_session=test_db_session,
        current_user=user,
        session_id=unique_session_id,
    )
    assert results_payload["errors"] == []
    assert [recipe["source"]["source"]["provenance"]["kind"] for recipe in results_payload["recipes"][:2]] == [
        "library_authored",
        "library_authored",
    ]
    assert all(
        recipe["source"]["source"]["provenance"]["kind"] == "generated"
        for recipe in results_payload["recipes"][2:]
    )
    assert {
        recipe["source"]["source"]["provenance"]["recipe_id"]
        for recipe in results_payload["recipes"][:2]
    } == {str(seeded_one.recipe_id), str(seeded_two.recipe_id)}
    assert all(
        recipe["source"]["source"]["provenance"]["cookbook_id"] == str(cookbook.cookbook_id)
        for recipe in results_payload["recipes"][:2]
    )
