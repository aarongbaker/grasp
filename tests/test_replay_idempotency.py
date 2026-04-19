"""
tests/test_replay_idempotency.py
Unit tests for the task-level replay guard in _run_pipeline_async.

All tests are mock-based — no real DB, Redis, or Celery worker required.

What is tested:
  - For each of the four terminal statuses (COMPLETE, PARTIAL, FAILED, CANCELLED),
    a redelivered task returns early without calling graph.ainvoke or finalise_session,
    and calls engine.dispose() to release resources.
  - For GENERATING status the guard does NOT fire, meaning the function continues
    past the terminal check into the normal pipeline flow.

Mock strategy:
  _run_pipeline_async uses deferred imports at function scope, so patches must
  target the source modules (not app.workers.tasks.*). Each import path is patched
  at the module where the name lives so the 'from X import Y' inside the function
  resolves to the mock.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import SessionStatus
from app.workers.tasks import _run_pipeline_async

# ── helpers ──────────────────────────────────────────────────────────────────


def _session_mock(status: SessionStatus) -> MagicMock:
    """Return a minimal Session-like mock with the given status string."""
    s = MagicMock()
    # DB returns status as a plain string; the guard uses set membership against
    # SessionStatus enum values (which are str subclasses and compare equal).
    s.status = status.value
    s.concept_json = {"free_text": "replay test"}
    return s


def _user_mock() -> MagicMock:
    u = MagicMock()
    u.rag_owner_key = "test-owner-key"
    u.kitchen_config_id = uuid.uuid4()
    return u


def _async_cm(aenter_value: object) -> MagicMock:
    """Build a MagicMock that behaves as an async context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=aenter_value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _build_mocks(session_status: SessionStatus):
    """
    Return a tuple of (patches_dict, engine_mock, graph_mock, finalise_mock)
    ready for use in a 'with patch(...)' block.

    The checkpointer, engine, sessionmaker, and db.get() are all wired so that
    _run_pipeline_async can reach the terminal-status check without crashing.
    """
    engine = AsyncMock()
    engine.dispose = AsyncMock(return_value=None)

    # checkpointer — returned by AsyncPostgresSaver.from_conn_string(url)
    checkpointer = AsyncMock()
    checkpointer.setup = AsyncMock(return_value=None)
    saver_cm = _async_cm(checkpointer)

    mock_saver_class = MagicMock()
    mock_saver_class.from_conn_string = MagicMock(return_value=saver_cm)

    # graph — returned by build_grasp_graph(checkpointer)
    graph = AsyncMock()
    graph.ainvoke = AsyncMock(return_value={"schedule": None, "errors": []})
    mock_build_graph = MagicMock(return_value=graph)

    # engine factory
    mock_create_engine = MagicMock(return_value=engine)

    # db session
    db = AsyncMock()
    db.get = AsyncMock(return_value=_session_mock(session_status))
    db.execute = AsyncMock()
    db_cm = _async_cm(db)

    mock_session_local = MagicMock(return_value=db_cm)
    mock_sessionmaker = MagicMock(return_value=mock_session_local)

    # finalise_session
    mock_finalise = AsyncMock(return_value=None)

    patches = {
        "langgraph.checkpoint.postgres.aio.AsyncPostgresSaver": mock_saver_class,
        "sqlalchemy.ext.asyncio.create_async_engine": mock_create_engine,
        "sqlalchemy.orm.sessionmaker": mock_sessionmaker,
        "app.graph.graph.build_grasp_graph": mock_build_graph,
        "app.core.status.finalise_session": mock_finalise,
    }
    return patches, engine, graph, mock_finalise


# ── terminal-status guard tests ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "terminal_status",
    [
        SessionStatus.COMPLETE,
        SessionStatus.PARTIAL,
        SessionStatus.FAILED,
        SessionStatus.CANCELLED,
    ],
)
async def test_replay_guard_does_not_invoke_graph_for_terminal_session(terminal_status):
    """
    A redelivered task for a session already in a terminal status must return
    early — graph.ainvoke and finalise_session must not be called, and
    engine.dispose() must be called to release the connection.
    """
    session_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    patches, engine, graph, mock_finalise = _build_mocks(terminal_status)

    with (
        patch("langgraph.checkpoint.postgres.aio.AsyncPostgresSaver", patches["langgraph.checkpoint.postgres.aio.AsyncPostgresSaver"]),
        patch("sqlalchemy.ext.asyncio.create_async_engine", patches["sqlalchemy.ext.asyncio.create_async_engine"]),
        patch("sqlalchemy.orm.sessionmaker", patches["sqlalchemy.orm.sessionmaker"]),
        patch("app.graph.graph.build_grasp_graph", patches["app.graph.graph.build_grasp_graph"]),
        patch("app.core.status.finalise_session", patches["app.core.status.finalise_session"]),
    ):
        await _run_pipeline_async(session_id, user_id)

    graph.ainvoke.assert_not_called()
    mock_finalise.assert_not_called()
    engine.dispose.assert_called_once()


# ── GENERATING: guard must not fire ──────────────────────────────────────────


async def test_replay_guard_does_not_block_generating_session():
    """
    A session in GENERATING status is a normal in-progress run — the replay
    guard must not fire. Verify that graph.ainvoke IS called.
    """
    session_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    engine = AsyncMock()
    engine.dispose = AsyncMock(return_value=None)

    checkpointer = AsyncMock()
    checkpointer.setup = AsyncMock(return_value=None)
    saver_cm = _async_cm(checkpointer)
    mock_saver_class = MagicMock()
    mock_saver_class.from_conn_string = MagicMock(return_value=saver_cm)

    graph = AsyncMock()
    graph.ainvoke = AsyncMock(return_value={"schedule": None, "errors": []})
    mock_build_graph = MagicMock(return_value=graph)

    mock_create_engine = MagicMock(return_value=engine)

    # db.get side_effect: first call → session (GENERATING), second → user,
    # third → kitchen (None is fine — build_session_initial_state handles it).
    user = _user_mock()
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[
        _session_mock(SessionStatus.GENERATING),  # Session lookup
        user,                                      # UserProfile lookup
        None,                                      # KitchenConfig lookup (None → {})
    ])
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[])
    execute_result = MagicMock()
    execute_result.scalars = MagicMock(return_value=scalars_mock)
    db.execute = AsyncMock(return_value=execute_result)

    db_cm = _async_cm(db)
    mock_session_local = MagicMock(return_value=db_cm)
    mock_sessionmaker = MagicMock(return_value=mock_session_local)

    mock_finalise = AsyncMock(return_value=None)

    # build_session_initial_state needs a valid DinnerConcept — patch it.
    mock_initial_state = {
        "concept": {},
        "user_id": user_id,
        "rag_owner_key": "test-owner-key",
        "kitchen_config": {},
        "equipment": [],
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
        "errors": [],
    }
    mock_build_initial_state = MagicMock(return_value=(None, mock_initial_state))

    with (
        patch("langgraph.checkpoint.postgres.aio.AsyncPostgresSaver", mock_saver_class),
        patch("sqlalchemy.ext.asyncio.create_async_engine", mock_create_engine),
        patch("sqlalchemy.orm.sessionmaker", mock_sessionmaker),
        patch("app.graph.graph.build_grasp_graph", mock_build_graph),
        patch("app.core.status.finalise_session", mock_finalise),
        patch("app.models.pipeline.build_session_initial_state", mock_build_initial_state),
    ):
        await _run_pipeline_async(session_id, user_id)

    graph.ainvoke.assert_called_once()
    mock_finalise.assert_called_once()
