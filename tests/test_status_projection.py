"""
tests/test_status_projection.py
Unit tests for core/status.py — status_projection() derivation logic.

Tests each pipeline stage by mocking graph.aget_state() to return
controlled state snapshots. No real LangGraph graph needed.

Covers all 5 derivation rules:
  empty state         → GENERATING
  raw_recipes         → ENRICHING
  enriched_recipes    → VALIDATING
  validated_recipes   → SCHEDULING
  recipe_dags         → SCHEDULING
  merged_dag          → SCHEDULING
  exception           → GENERATING (safe fallback)
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.status import status_projection
from app.models.enums import MealType, Occasion, SessionStatus
from app.models.pipeline import (
    DinnerConcept,
    PlannerLibraryAuthoredRecipeAnchor,
    PlannerLibraryCookbookTarget,
    SelectedCookbookRecipe,
    build_initial_pipeline_state,
    build_session_initial_state,
)


def _make_mock_graph(state_values: dict):
    """Create a mock graph whose aget_state returns a snapshot with given values."""
    graph = AsyncMock()
    snapshot = MagicMock()
    snapshot.values = state_values
    graph.aget_state.return_value = snapshot
    return graph


def _make_mock_graph_none():
    """Mock graph that returns None from aget_state (no checkpoint)."""
    graph = AsyncMock()
    graph.aget_state.return_value = None
    return graph


def _make_mock_graph_error():
    """Mock graph that raises when aget_state is called."""
    graph = AsyncMock()
    graph.aget_state.side_effect = Exception("checkpoint unavailable")
    return graph


@pytest.mark.asyncio
async def test_build_session_initial_state_normalizes_uuid_and_enum_planner_payloads_for_checkpoint():
    concept_payload = {
        "free_text": "Build a dinner around my braise.",
        "guest_count": 4,
        "meal_type": MealType.DINNER.value,
        "occasion": Occasion.DINNER_PARTY.value,
        "dietary_restrictions": [],
        "serving_time": "19:00",
        "concept_source": "planner_cookbook_target",
        "planner_cookbook_target": {
            "cookbook_id": str(uuid.uuid4()),
            "name": "Sunday Suppers",
            "description": "Braises and sides",
            "mode": "strict",
        },
    }

    concept, initial_state = build_session_initial_state(
        concept_payload=concept_payload,
        user_id=str(uuid.uuid4()),
        rag_owner_key="email:test-chef",
        kitchen_config={"max_burners": 4},
        equipment=[],
    )

    assert isinstance(initial_state["concept"]["planner_cookbook_target"]["cookbook_id"], str)
    assert initial_state["concept"]["planner_cookbook_target"]["mode"] == "strict"
    assert concept.model_dump(mode="json")["planner_cookbook_target"]["mode"] == "strict"


# ─────────────────────────────────────────────────────────────────────────────
# Derivation rules
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_state_returns_generating():
    graph = _make_mock_graph({})
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.GENERATING


@pytest.mark.asyncio
async def test_none_snapshot_returns_generating():
    graph = _make_mock_graph_none()
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.GENERATING


@pytest.mark.asyncio
async def test_raw_recipes_returns_enriching():
    """raw_recipes populated means generator ran → enricher is next."""
    graph = _make_mock_graph({"raw_recipes": [{"name": "test"}]})
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.ENRICHING


@pytest.mark.asyncio
async def test_enriched_recipes_returns_validating():
    """enriched_recipes populated means enricher ran → validator is next."""
    graph = _make_mock_graph(
        {
            "raw_recipes": [{"name": "test"}],
            "enriched_recipes": [{"source": {}, "steps": []}],
        }
    )
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.VALIDATING


@pytest.mark.asyncio
async def test_validated_recipes_returns_scheduling():
    """validated_recipes populated → SCHEDULING (dag_builder is next)."""
    graph = _make_mock_graph(
        {
            "raw_recipes": [{"name": "test"}],
            "enriched_recipes": [{"source": {}, "steps": []}],
            "validated_recipes": [{"source": {}, "validated_at": "2024-01-01"}],
        }
    )
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.SCHEDULING


@pytest.mark.asyncio
async def test_recipe_dags_returns_scheduling():
    """recipe_dags populated → SCHEDULING (dag_merger is next)."""
    graph = _make_mock_graph(
        {
            "raw_recipes": [{"name": "test"}],
            "enriched_recipes": [{"source": {}, "steps": []}],
            "validated_recipes": [{"source": {}, "validated_at": "2024-01-01"}],
            "recipe_dags": [{"recipe_name": "test", "edges": []}],
        }
    )
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.SCHEDULING


@pytest.mark.asyncio
async def test_merged_dag_returns_scheduling():
    """merged_dag populated → SCHEDULING (renderer is next)."""
    graph = _make_mock_graph(
        {
            "raw_recipes": [{"name": "test"}],
            "merged_dag": {"scheduled_steps": [], "total_duration_minutes": 100},
        }
    )
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.SCHEDULING


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases / fallbacks
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exception_returns_generating():
    """If graph.aget_state raises, safe fallback is GENERATING."""
    graph = _make_mock_graph_error()
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.GENERATING


@pytest.mark.asyncio
async def test_empty_lists_treated_as_not_populated():
    """Empty lists are falsy — should not advance status."""
    graph = _make_mock_graph(
        {
            "raw_recipes": [],
            "enriched_recipes": [],
            "validated_recipes": [],
            "recipe_dags": [],
            "merged_dag": None,
        }
    )
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.GENERATING


def test_build_initial_pipeline_state_preserves_planner_authored_anchor_without_status_fields():
    recipe_id = uuid.uuid4()
    concept = DinnerConcept(
        free_text="Plan a menu around my saved braise.",
        guest_count=6,
        meal_type=MealType.DINNER,
        occasion=Occasion.DINNER_PARTY,
        concept_source="planner_authored_anchor",
        planner_authored_recipe_anchor=PlannerLibraryAuthoredRecipeAnchor(
            recipe_id=recipe_id,
            title="Sunday Braise",
        ),
    )

    state = build_initial_pipeline_state(
        concept=concept,
        user_id="user-123",
        rag_owner_key="owner-123",
        kitchen_config={"max_burners": 4},
        equipment=[{"name": "Dutch oven"}],
    )

    assert state["concept"]["concept_source"] == "planner_authored_anchor"
    assert state["concept"]["planner_authored_recipe_anchor"] == {
        "recipe_id": str(recipe_id),
        "title": "Sunday Braise",
    }
    assert state["concept"]["planner_cookbook_target"] is None
    assert state["raw_recipes"] == []
    assert state["errors"] == []


def test_build_session_initial_state_preserves_planner_cookbook_target():
    cookbook_id = uuid.uuid4()
    concept_payload = {
        "free_text": "Plan within my plated-dessert shelf.",
        "guest_count": 8,
        "meal_type": "dinner",
        "occasion": "dinner_party",
        "dietary_restrictions": [],
        "concept_source": "planner_cookbook_target",
        "planner_cookbook_target": {
            "cookbook_id": str(cookbook_id),
            "name": "Desserts",
            "description": "Late-course authored recipes.",
            "mode": "strict",
        },
    }

    concept, state = build_session_initial_state(
        concept_payload=concept_payload,
        user_id="user-123",
        rag_owner_key="owner-123",
        kitchen_config={"max_burners": 4},
        equipment=[{"name": "Dutch oven"}],
    )

    assert concept.concept_source == "planner_cookbook_target"
    assert concept.planner_cookbook_target == PlannerLibraryCookbookTarget(
        cookbook_id=cookbook_id,
        name="Desserts",
        description="Late-course authored recipes.",
        mode="strict",
    )
    assert state["concept"]["planner_authored_recipe_anchor"] is None
    assert state["concept"]["planner_cookbook_target"] == {
        "cookbook_id": str(cookbook_id),
        "name": "Desserts",
        "description": "Late-course authored recipes.",
        "mode": "strict",
    }
    assert state["raw_recipes"] == []
    assert state["errors"] == []


def test_build_session_initial_state_rejects_planner_cookbook_target_when_mixed_with_runtime_cookbook_shape():
    concept_payload = {
        "free_text": "Plan within my plated-dessert shelf.",
        "guest_count": 8,
        "meal_type": "dinner",
        "occasion": "dinner_party",
        "dietary_restrictions": [],
        "concept_source": "planner_cookbook_target",
        "planner_cookbook_target": {
            "cookbook_id": str(uuid.uuid4()),
            "name": "Desserts",
            "description": "Late-course authored recipes.",
            "mode": "cookbook_biased",
        },
        "selected_recipes": [
            {
                "chunk_id": str(uuid.uuid4()),
                "book_id": str(uuid.uuid4()),
                "book_title": "Book One",
                "text": "Recipe One\nMethod:\n1. Prep\n2. Cook\n3. Serve",
                "chapter": "Chapter One",
                "page_number": 10,
            }
        ],
    }

    with pytest.raises(Exception, match="selected_recipes is only allowed when concept_source is 'cookbook'"):
        build_session_initial_state(
            concept_payload=concept_payload,
            user_id="user-123",
            rag_owner_key="owner-123",
            kitchen_config={},
            equipment=[],
        )


def test_build_session_initial_state_rejects_planner_authored_anchor_when_mixed_with_runtime_authored_shape():
    concept_payload = {
        "free_text": "Plan around my saved braise.",
        "guest_count": 4,
        "meal_type": "dinner",
        "occasion": "casual",
        "dietary_restrictions": [],
        "concept_source": "planner_authored_anchor",
        "planner_authored_recipe_anchor": {
            "recipe_id": str(uuid.uuid4()),
            "title": "Sunday Braise",
        },
        "selected_authored_recipe": {
            "recipe_id": str(uuid.uuid4()),
            "title": "Should not coexist",
        },
    }

    with pytest.raises(Exception, match="selected_authored_recipe is only allowed when concept_source is 'authored'"):
        build_session_initial_state(
            concept_payload=concept_payload,
            user_id="user-123",
            rag_owner_key="owner-123",
            kitchen_config={},
            equipment=[],
        )


def test_build_initial_pipeline_state_preserves_cookbook_concept_without_status_fields():
    concept = DinnerConcept(
        free_text="Cookbook-selected recipes: Roast chicken.",
        guest_count=4,
        meal_type=MealType.DINNER,
        occasion=Occasion.DINNER_PARTY,
        concept_source="cookbook",
        selected_recipes=[
            SelectedCookbookRecipe(
                chunk_id=uuid.uuid4(),
                book_id=uuid.uuid4(),
                book_title="The French Laundry Cookbook",
                text="Roast Chicken\nMethod:\n1. Prep\n2. Roast\n3. Rest",
                chapter="Poultry",
                page_number=87,
            )
        ],
    )

    kitchen = {"max_burners": 4}
    equipment = [{"name": "Dutch oven"}]

    state = build_initial_pipeline_state(
        concept=concept,
        user_id="user-123",
        rag_owner_key="owner-123",
        kitchen_config=kitchen,
        equipment=equipment,
    )

    assert state["concept"]["concept_source"] == "cookbook"
    assert len(state["concept"]["selected_recipes"]) == 1
    assert state["raw_recipes"] == []
    assert state["enriched_recipes"] == []
    assert state["validated_recipes"] == []
    assert state["errors"] == []


def test_build_session_initial_state_validates_and_preserves_ordered_cookbook_selection():
    first_chunk = uuid.uuid4()
    second_chunk = uuid.uuid4()
    concept_payload = {
        "free_text": "Cookbook-selected recipes: Roast chicken, pommes puree.",
        "guest_count": 4,
        "meal_type": "dinner",
        "occasion": "dinner_party",
        "dietary_restrictions": [],
        "concept_source": "cookbook",
        "selected_recipes": [
            {
                "chunk_id": str(first_chunk),
                "book_id": str(uuid.uuid4()),
                "book_title": "Book One",
                "text": "Recipe One\nMethod:\n1. Prep\n2. Cook\n3. Serve",
                "chapter": "Chapter One",
                "page_number": 10,
            },
            {
                "chunk_id": str(second_chunk),
                "book_id": str(uuid.uuid4()),
                "book_title": "Book Two",
                "text": "Recipe Two\nMethod:\n1. Prep\n2. Cook\n3. Serve",
                "chapter": "Chapter Two",
                "page_number": 20,
            },
        ],
    }

    concept, state = build_session_initial_state(
        concept_payload=concept_payload,
        user_id="user-123",
        rag_owner_key="owner-123",
        kitchen_config={"max_burners": 4},
        equipment=[{"name": "Dutch oven"}],
    )

    assert concept.concept_source == "cookbook"
    assert [recipe.chunk_id for recipe in concept.selected_recipes] == [first_chunk, second_chunk]
    assert [recipe["chunk_id"] for recipe in state["concept"]["selected_recipes"]] == [str(first_chunk), str(second_chunk)]
    assert state["raw_recipes"] == []
    assert state["errors"] == []


def test_build_session_initial_state_rejects_cookbook_concept_without_selected_recipes():
    concept_payload = {
        "free_text": "Cookbook-selected recipes.",
        "guest_count": 4,
        "meal_type": "dinner",
        "occasion": "dinner_party",
        "dietary_restrictions": [],
        "concept_source": "cookbook",
        "selected_recipes": [],
    }

    with pytest.raises(Exception, match="selected_recipes is required"):
        build_session_initial_state(
            concept_payload=concept_payload,
            user_id="user-123",
            rag_owner_key="owner-123",
            kitchen_config={},
            equipment=[],
        )


@pytest.mark.asyncio
async def test_cookbook_raw_recipes_still_project_enriching():
    """Cookbook-seeded raw_recipes should use the normal ENRICHING projection."""
    graph = _make_mock_graph(
        {
            "raw_recipes": [
                {
                    "name": "Roast Chicken with Bread Salad",
                    "steps": ["Prep", "Roast", "Rest"],
                }
            ]
        }
    )
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.ENRICHING


@pytest.mark.asyncio
async def test_planner_authored_anchor_raw_recipes_still_project_enriching_without_planner_runtime_fields():
    """Planner-authored mixed-origin runs should need only raw_recipes to project ENRICHING."""
    recipe_id = uuid.uuid4()
    graph = _make_mock_graph(
        {
            "concept": {
                "free_text": "Plan a menu around my saved braise.",
                "guest_count": 6,
                "meal_type": "dinner",
                "occasion": "dinner_party",
                "dietary_restrictions": [],
                "concept_source": "planner_authored_anchor",
                "planner_authored_recipe_anchor": {
                    "recipe_id": str(recipe_id),
                    "title": "Sunday Braise",
                },
            },
            "raw_recipes": [
                {
                    "name": "Sunday Braise",
                    "steps": ["Brown", "Braise", "Rest"],
                },
                {
                    "name": "Charred Chicory Salad",
                    "steps": ["Prep", "Char", "Dress"],
                },
            ],
        }
    )

    status = await status_projection(uuid.uuid4(), graph)

    assert status == SessionStatus.ENRICHING


@pytest.mark.asyncio
async def test_planner_cookbook_target_raw_recipes_still_project_enriching_without_planner_runtime_fields():
    """Planner-cookbook mixed-origin runs should project ENRICHING from raw_recipes alone."""
    cookbook_id = uuid.uuid4()
    graph = _make_mock_graph(
        {
            "concept": {
                "free_text": "Plan dinner using dishes from my market supper club cookbook.",
                "guest_count": 4,
                "meal_type": "dinner",
                "occasion": "dinner_party",
                "dietary_restrictions": [],
                "concept_source": "planner_cookbook_target",
                "planner_cookbook_target": {
                    "cookbook_id": str(cookbook_id),
                    "name": "Market Supper Club",
                    "description": "Late-summer dinner drafts.",
                    "mode": "cookbook_biased",
                },
            },
            "raw_recipes": [
                {
                    "name": "Braised Fennel",
                    "steps": ["Prep", "Braise", "Finish"],
                },
                {
                    "name": "Olive Oil Cake",
                    "steps": ["Mix", "Bake", "Cool"],
                },
                {
                    "name": "Charred Chicory Salad",
                    "steps": ["Prep", "Char", "Dress"],
                },
            ],
        }
    )

    status = await status_projection(uuid.uuid4(), graph)

    assert status == SessionStatus.ENRICHING


@pytest.mark.asyncio
async def test_most_advanced_field_wins():
    """When multiple fields populated, the most advanced one determines status."""
    graph = _make_mock_graph(
        {
            "raw_recipes": [{"name": "a"}],
            "enriched_recipes": [{"source": {}}],
            "validated_recipes": [{"source": {}}],
            "merged_dag": {"scheduled_steps": []},
        }
    )
    status = await status_projection(uuid.uuid4(), graph)
    # merged_dag is most advanced → SCHEDULING (not VALIDATING or ENRICHING)
    assert status == SessionStatus.SCHEDULING
