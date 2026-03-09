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
    instead of calling Claude. The patch stays active for the entire test session.
    All generator node logic (prompt building, result formatting) still runs for
    real — only the LLM call is bypassed.
    """
    from unittest.mock import patch, MagicMock, AsyncMock
    from graph.nodes.generator import RecipeGenerationOutput
    from tests.fixtures.recipes import (
        RAW_SHORT_RIBS,
        RAW_POMMES_PUREE,
        RAW_CHOCOLATE_FONDANT,
    )

    # Build mock chain that returns fixture recipes
    mock_output = RecipeGenerationOutput(
        recipes=[RAW_SHORT_RIBS, RAW_POMMES_PUREE, RAW_CHOCOLATE_FONDANT]
    )
    mock_chain = AsyncMock()
    mock_chain.ainvoke.return_value = mock_output

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain

    with patch("graph.nodes.generator._create_llm", return_value=mock_llm):
        from graph.graph import build_grasp_graph
        graph = build_grasp_graph(test_checkpointer)
        yield graph


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
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
        "errors": [],
        "test_mode": None,
    }
