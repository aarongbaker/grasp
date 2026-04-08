"""
tests/conftest.py
Session-scoped fixtures for Phase 3 integration tests.

CRITICAL details:
1. Test DB is port 5433 (separate from dev DB on 5432) — checkpoint data
   from tests never bleeds into the dev environment.
2. checkpointer.setup() creates the LangGraph checkpoint tables. Must run
   before any graph.ainvoke() call.
3. Unique session_id per test as thread_id. Ensures checkpoint data from
   one test never affects another. Enforced by the unique_session_id fixture.
4. finalise_session() is called explicitly after each ainvoke() in tests —
   the same function used in the production Celery task wrapper.
5. Tests call graph.ainvoke() directly — no Redis or Celery worker needed
   in Phase 3.

Two async session scopes:
  - "session" scope: checkpointer + graph (expensive setup, shared across tests)
  - "function" scope: DB session (fresh per test for isolation)

Mock architecture (Phase 4-5):
  Generator mock: patches _create_llm → returns 3 fixture RawRecipes.
  Enricher mock: patches _create_llm + _retrieve_rag_context → returns fixture
    EnrichedRecipes based on recipe name in the LLM message. Per-test failure
    control via _enricher_skip_recipes set + enricher_fail_fondant fixture.
"""

import asyncio
import os
import uuid

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from app.api.routes.auth import _build_access_token
from app.core.settings import get_settings
from app.models.user import UserProfile

settings = get_settings()
TEST_DB_SKIP_REASON = "Postgres test database 'grasp_test' is not available locally — skipping DB-backed integration tests"


def _ensure_test_postgres_available() -> None:
    try:
        with psycopg.connect(settings.test_langgraph_checkpoint_url, connect_timeout=2):
            return
    except psycopg.Error:
        pytest.skip(TEST_DB_SKIP_REASON)


# ── Enricher mock control ────────────────────────────────────────────────────
# Module-level set of recipe names that the enricher mock should simulate
# failure for. Tests add names via the enricher_fail_fondant fixture.
_enricher_skip_recipes: set[str] = set()

# When True, the enricher mock returns cyclic step data for ALL recipes.
# Used by test_run3 (fatal error) — cycles are caught by the real DAG builder.
_enricher_cyclic_mode: bool = False

# ── Generator mock control ───────────────────────────────────────────────────
# When True, the generator mock returns FT (finish-together) test recipes
# instead of the default 3-recipe menu. Used by S03 integration tests.
_generator_ft_mode: bool = False


@pytest.fixture(scope="session")
def event_loop_policy():
    """Provide a single event-loop policy instance for the test session.

    pytest-asyncio owns per-test loop creation in auto mode. Returning the
    default policy here avoids the deprecated custom event_loop fixture while
    still giving the plugin a stable policy under Python 3.12.
    """
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture(scope="session")
async def test_checkpointer():
    """
    Session-scoped AsyncPostgresSaver pointed at the test DB (port 5433).
    checkpointer.setup() creates the LangGraph checkpoint tables if they
    don't exist. This is idempotent — safe to call on every test run.

    Uses psycopg3 connection pool (not SQLAlchemy — different driver).
    This is intentional: LangGraph's PostgresSaver requires psycopg3 directly.
    """
    _ensure_test_postgres_available()
    try:
        import psycopg_pool
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(settings.test_langgraph_checkpoint_url) as checkpointer:
            await checkpointer.setup()
            yield checkpointer

    except ImportError:
        # Fall back to MemorySaver if langgraph-checkpoint-postgres not installed
        # This allows tests to run without Postgres in CI environments
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()
        yield checkpointer


@pytest_asyncio.fixture(scope="session")
async def compiled_graph(test_checkpointer):
    """
    Session-scoped compiled LangGraph graph. Shared across all Phase 3 tests.

    Phase 4: patches _create_llm in the real generator to return fixture recipes
    instead of calling Claude.
    Phase 5: patches _create_llm and _retrieve_rag_context in the real enricher
    to return fixture EnrichedRecipe data without calling Claude or Pinecone.
    The enricher mock's side_effect inspects the HumanMessage to determine
    which recipe is being enriched, and checks _enricher_skip_recipes to
    simulate per-recipe failures for specific tests.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.graph.nodes.enricher import StepEnrichmentOutput
    from app.graph.nodes.generator import RecipeGenerationOutput
    from app.graph.nodes.renderer import ScheduleSummaryOutput
    from tests.fixtures.recipes import (
        CYCLIC_STEPS_FONDANT,
        CYCLIC_STEPS_POMMES_PUREE,
        CYCLIC_STEPS_SHORT_RIBS,
        ENRICHED_CHOCOLATE_FONDANT,
        ENRICHED_FT_RECIPE_A,
        ENRICHED_FT_RECIPE_B,
        ENRICHED_FT_RECIPE_C,
        ENRICHED_POMMES_PUREE,
        ENRICHED_SHORT_RIBS,
        RAW_CHOCOLATE_FONDANT,
        RAW_FT_RECIPE_A,
        RAW_FT_RECIPE_B,
        RAW_FT_RECIPE_C,
        RAW_POMMES_PUREE,
        RAW_SHORT_RIBS,
    )
    from tests.fixtures.schedules import (
        NATURAL_LANGUAGE_SCHEDULE_FULL,
        NATURAL_LANGUAGE_SCHEDULE_TWO_RECIPE,
    )

    # ── Generator mock (Phase 4) ─────────────────────────────────────────────
    # Returns default 3-recipe menu OR FT test recipes based on _generator_ft_mode
    gen_default_output = RecipeGenerationOutput(recipes=[RAW_SHORT_RIBS, RAW_POMMES_PUREE, RAW_CHOCOLATE_FONDANT])
    gen_ft_output = RecipeGenerationOutput(recipes=[RAW_FT_RECIPE_A, RAW_FT_RECIPE_B, RAW_FT_RECIPE_C])

    async def _generator_ainvoke_side_effect(_):
        """Return default or FT recipes based on _generator_ft_mode flag."""
        if _generator_ft_mode:
            return gen_ft_output
        return gen_default_output

    gen_mock_chain = AsyncMock()
    gen_mock_chain.ainvoke = AsyncMock(side_effect=_generator_ainvoke_side_effect)

    gen_mock_llm = MagicMock()
    gen_mock_llm.with_structured_output.return_value = gen_mock_chain

    # ── Enricher mock (Phase 5) ──────────────────────────────────────────────
    # Map recipe names to their fixture enrichment outputs
    _enricher_fixture_map = {
        "Braised Short Ribs": ENRICHED_SHORT_RIBS,
        "Pommes Puree": ENRICHED_POMMES_PUREE,
        "Chocolate Fondant": ENRICHED_CHOCOLATE_FONDANT,
        # Finish-together test fixtures (S03 integration tests)
        "Recipe A Long Braise": ENRICHED_FT_RECIPE_A,
        "Recipe B Quick Saute": ENRICHED_FT_RECIPE_B,
        "Recipe C Medium Roast": ENRICHED_FT_RECIPE_C,
    }

    # Map recipe names to cyclic step data (Phase 6 fatal error test)
    _enricher_cyclic_map = {
        "Braised Short Ribs": CYCLIC_STEPS_SHORT_RIBS,
        "Pommes Puree": CYCLIC_STEPS_POMMES_PUREE,
        "Chocolate Fondant": CYCLIC_STEPS_FONDANT,
    }

    async def _enricher_ainvoke_side_effect(messages):
        """Return fixture StepEnrichmentOutput based on recipe name in message."""
        human_content = messages[1].content

        # Check if any skip recipe name appears in the message
        for skip_name in _enricher_skip_recipes:
            if skip_name in human_content:
                raise Exception(f"Simulated enrichment failure for '{skip_name}'")

        # Cyclic mode: return steps with circular dependencies
        if _enricher_cyclic_mode:
            for recipe_name, cyclic_steps in _enricher_cyclic_map.items():
                if recipe_name in human_content:
                    return StepEnrichmentOutput(
                        steps=cyclic_steps,
                        chef_notes="Cyclic fixture",
                        techniques_used=[],
                    )

        # Normal mode: match against fixture recipes
        for recipe_name, enriched in _enricher_fixture_map.items():
            if recipe_name in human_content:
                return StepEnrichmentOutput(
                    steps=enriched.steps,
                    chef_notes=enriched.chef_notes,
                    techniques_used=enriched.techniques_used,
                )

        raise Exception(f"Enricher mock: no fixture match for message: {human_content[:80]}")

    enricher_mock_chain = AsyncMock()
    enricher_mock_chain.ainvoke = AsyncMock(side_effect=_enricher_ainvoke_side_effect)

    enricher_mock_llm = MagicMock()
    enricher_mock_llm.with_structured_output.return_value = enricher_mock_chain

    # ── Renderer mock (Phase 7) ──────────────────────────────────────────────
    # The renderer's LLM only generates summary + error_summary.
    # Timeline construction is deterministic (no LLM). The mock inspects
    # the HumanMessage step count to decide which fixture summary to return.

    async def _renderer_ainvoke_side_effect(messages):
        """Return fixture ScheduleSummaryOutput based on step count in message."""
        human_content = messages[1].content
        # 12-step = full (3 recipes), 7-step = two-recipe (fondant dropped)
        if "12-step" in human_content:
            return ScheduleSummaryOutput(
                summary=NATURAL_LANGUAGE_SCHEDULE_FULL.summary,
                error_summary=None,
            )
        elif "7-step" in human_content:
            return ScheduleSummaryOutput(
                summary=NATURAL_LANGUAGE_SCHEDULE_TWO_RECIPE.summary,
                error_summary=NATURAL_LANGUAGE_SCHEDULE_TWO_RECIPE.error_summary,
            )
        # Fallback: return a generic summary for any step count
        return ScheduleSummaryOutput(
            summary="Mock schedule summary.",
            error_summary=None,
        )

    renderer_mock_chain = AsyncMock()
    renderer_mock_chain.ainvoke = AsyncMock(side_effect=_renderer_ainvoke_side_effect)

    renderer_mock_llm = MagicMock()
    renderer_mock_llm.with_structured_output.return_value = renderer_mock_chain

    # ── Build graph with all mocks active ────────────────────────────────────
    with (
        patch("app.graph.nodes.generator._create_llm", return_value=gen_mock_llm),
        patch("app.graph.nodes.enricher._create_llm", return_value=enricher_mock_llm),
        patch("app.graph.nodes.enricher._retrieve_rag_context", return_value=[]),
        patch("app.graph.nodes.renderer._create_llm", return_value=renderer_mock_llm),
    ):
        from app.graph.graph import build_grasp_graph

        graph = build_grasp_graph(test_checkpointer)
        yield graph


@pytest.fixture
def enricher_fail_fondant():
    """
    Function-scoped fixture that makes the enricher mock raise for Chocolate
    Fondant. Used by test_run2 (recoverable error) to simulate per-recipe
    RAG enrichment failure. Controls per-recipe enricher failure behavior
    for test_run2 (recoverable error).
    """
    _enricher_skip_recipes.add("Chocolate Fondant")
    yield
    _enricher_skip_recipes.discard("Chocolate Fondant")


@pytest.fixture
def enricher_return_cyclic():
    """
    Function-scoped fixture that makes the enricher mock return cyclic step
    data for ALL recipes. Used by test_run3 (fatal error) — the real DAG
    builder catches cycles via NetworkX and returns DEPENDENCY_RESOLUTION
    errors. All recipes fail → fatal (recoverable=False).
    """
    global _enricher_cyclic_mode
    _enricher_cyclic_mode = True
    yield
    _enricher_cyclic_mode = False


@pytest.fixture
def generator_ft_mode():
    """
    Function-scoped fixture that makes the generator mock return finish-together
    test recipes (RAW_FT_RECIPE_A/B/C) instead of the default 3-recipe menu.
    Used by S03 integration tests to verify finish-together scheduling.
    """
    global _generator_ft_mode
    _generator_ft_mode = True
    yield
    _generator_ft_mode = False


@pytest_asyncio.fixture(scope="session")
async def test_db_engine():
    """Session-scoped test DB engine. Creates all SQLModel tables once."""
    _ensure_test_postgres_available()
    engine = create_async_engine(settings.test_database_url, echo=False, poolclass=NullPool)

    # Import all SQLModel table models to register metadata
    import app.models.ingestion  # noqa: F401
    import app.models.session  # noqa: F401
    import app.models.user  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def test_db_session(test_db_engine):
    """Function-scoped DB session. Fresh engine per test to avoid asyncpg
    connection reuse issues ('another operation is in progress')."""
    engine = create_async_engine(
        settings.test_database_url,
        echo=False,
        poolclass=NullPool,
    )
    session = AsyncSession(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def admin_user(test_db_session):
    """Persist the configured admin user for route-contract tests."""
    email = f"admin-{uuid.uuid4()}@example.com"
    user = UserProfile(
        user_id=uuid.uuid4(),
        name="Admin User",
        email=email,
        rag_owner_key=UserProfile.build_rag_owner_key(email),
        password_hash="admin-hash",
    )
    test_db_session.add(user)
    await test_db_session.commit()
    await test_db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def non_admin_user(test_db_session):
    """Persist a non-admin authenticated caller for route-contract tests."""
    email = f"member-{uuid.uuid4()}@example.com"
    user = UserProfile(
        user_id=uuid.uuid4(),
        name="Non Admin User",
        email=email,
        rag_owner_key=UserProfile.build_rag_owner_key(email),
        password_hash="member-hash",
    )
    test_db_session.add(user)
    await test_db_session.commit()
    await test_db_session.refresh(user)
    return user


@pytest.fixture
def admin_route_settings(admin_user):
    """Settings fixture that marks the persisted admin user as the configured operator."""
    return get_settings().model_copy(update={"admin_email": admin_user.email})


@pytest.fixture
def access_token_for():
    """Build a real bearer token using the production JWT helper."""

    def _access_token_for(user: UserProfile, settings_override=None) -> str:
        active_settings = settings_override or get_settings()
        token, _expires_in = _build_access_token(str(user.user_id), user.email, active_settings)
        return token

    return _access_token_for


@pytest_asyncio.fixture
async def test_user_id(test_db_session):
    """Creates a UserProfile in the test DB and returns the user_id.
    Required because sessions.user_id has a FK to user_profiles."""
    from app.models.user import UserProfile

    user_id = uuid.uuid4()
    user = UserProfile(
        user_id=user_id,
        name="Test Chef",
        email=f"chef-{user_id}@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key(f"chef-{user_id}@test.com"),
    )
    test_db_session.add(user)
    await test_db_session.commit()
    return user_id


@pytest.fixture
def unique_session_id() -> uuid.UUID:
    """
    Unique session_id used as LangGraph thread_id.
    Guarantees checkpoint data from one test never bleeds into another.
    """
    return uuid.uuid4()


@pytest.fixture
def base_initial_state() -> dict:
    """
    Base GRASPState for all Phase 3 tests. Uses fixture dinner concept.
    Nodes populate raw_recipes, enriched_recipes, etc. from here.
    """
    from app.models.enums import MealType, Occasion
    from app.models.pipeline import DinnerConcept

    concept = DinnerConcept(
        free_text="A special dinner party with short ribs, potato puree, and chocolate fondant.",
        guest_count=4,
        meal_type=MealType.DINNER,
        occasion=Occasion.DINNER_PARTY,
        dietary_restrictions=[],
    )

    return {
        "concept": concept.model_dump(),
        "kitchen_config": {
            "max_burners": 4,
            "max_oven_racks": 2,
            "has_second_oven": False,
        },
        "equipment": [],
        "user_id": "",
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
        "errors": [],
    }
