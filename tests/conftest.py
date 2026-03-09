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
import uuid
import os
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from core.settings import get_settings

settings = get_settings()


# ── Enricher mock control ────────────────────────────────────────────────────
# Module-level set of recipe names that the enricher mock should simulate
# failure for. Tests add names via the enricher_fail_fondant fixture.
_enricher_skip_recipes: set[str] = set()


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop — required for session-scoped async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_checkpointer():
    """
    Session-scoped AsyncPostgresSaver pointed at the test DB (port 5433).
    checkpointer.setup() creates the LangGraph checkpoint tables if they
    don't exist. This is idempotent — safe to call on every test run.

    Uses psycopg3 connection pool (not SQLAlchemy — different driver).
    This is intentional: LangGraph's PostgresSaver requires psycopg3 directly.
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        import psycopg_pool

        async with AsyncPostgresSaver.from_conn_string(
            settings.test_langgraph_checkpoint_url
        ) as checkpointer:
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
    from unittest.mock import patch, MagicMock, AsyncMock
    from graph.nodes.generator import RecipeGenerationOutput
    from graph.nodes.enricher import StepEnrichmentOutput
    from tests.fixtures.recipes import (
        RAW_SHORT_RIBS,
        RAW_POMMES_PUREE,
        RAW_CHOCOLATE_FONDANT,
        ENRICHED_SHORT_RIBS,
        ENRICHED_POMMES_PUREE,
        ENRICHED_CHOCOLATE_FONDANT,
    )

    # ── Generator mock (Phase 4) ─────────────────────────────────────────────
    gen_mock_output = RecipeGenerationOutput(
        recipes=[RAW_SHORT_RIBS, RAW_POMMES_PUREE, RAW_CHOCOLATE_FONDANT]
    )
    gen_mock_chain = AsyncMock()
    gen_mock_chain.ainvoke.return_value = gen_mock_output

    gen_mock_llm = MagicMock()
    gen_mock_llm.with_structured_output.return_value = gen_mock_chain

    # ── Enricher mock (Phase 5) ──────────────────────────────────────────────
    # Map recipe names to their fixture enrichment outputs
    _enricher_fixture_map = {
        "Braised Short Ribs": ENRICHED_SHORT_RIBS,
        "Pommes Puree": ENRICHED_POMMES_PUREE,
        "Chocolate Fondant": ENRICHED_CHOCOLATE_FONDANT,
    }

    async def _enricher_ainvoke_side_effect(messages):
        """Return fixture StepEnrichmentOutput based on recipe name in message."""
        human_content = messages[1].content

        # Check if any skip recipe name appears in the message
        for skip_name in _enricher_skip_recipes:
            if skip_name in human_content:
                raise Exception(
                    f"Simulated enrichment failure for '{skip_name}'"
                )

        # Match against fixture recipes
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

    # ── Build graph with all mocks active ────────────────────────────────────
    with patch("graph.nodes.generator._create_llm", return_value=gen_mock_llm), \
         patch("graph.nodes.enricher._create_llm", return_value=enricher_mock_llm), \
         patch("graph.nodes.enricher._retrieve_rag_context", return_value=[]):
        from graph.graph import build_grasp_graph
        graph = build_grasp_graph(test_checkpointer)
        yield graph


@pytest.fixture
def enricher_fail_fondant():
    """
    Function-scoped fixture that makes the enricher mock raise for Chocolate
    Fondant. Used by test_run2 (recoverable error) to simulate per-recipe
    RAG enrichment failure. Replaces the old test_mode="recoverable_error"
    mechanism that lived in mock_enricher.py.
    """
    _enricher_skip_recipes.add("Chocolate Fondant")
    yield
    _enricher_skip_recipes.discard("Chocolate Fondant")


@pytest_asyncio.fixture(scope="session")
async def test_db_engine():
    """Session-scoped test DB engine. Creates all SQLModel tables once."""
    engine = create_async_engine(settings.test_database_url, echo=False, poolclass=NullPool)

    # Import all SQLModel table models to register metadata
    import models.user       # noqa: F401
    import models.session    # noqa: F401
    import models.ingestion  # noqa: F401

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
        settings.test_database_url, echo=False, poolclass=NullPool,
    )
    session = AsyncSession(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def test_user_id(test_db_session):
    """Creates a UserProfile in the test DB and returns the user_id.
    Required because sessions.user_id has a FK to user_profiles."""
    from models.user import UserProfile
    user_id = uuid.uuid4()
    user = UserProfile(
        user_id=user_id,
        name="Test Chef",
        email=f"chef-{user_id}@test.com",
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
    from models.pipeline import DinnerConcept
    from models.enums import MealType, Occasion

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
        "test_mode": None,
    }
