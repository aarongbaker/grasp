import sys
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from app.models.enums import EquipmentCategory, ErrorType, SessionStatus
from app.models.session import Session
from app.models.user import Equipment, KitchenConfig, UserProfile
from app.workers.tasks import _run_pipeline_async


class StubAsyncPostgresSaver:
    @classmethod
    def from_conn_string(cls, _conn_string):
        return cls()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def setup(self):
        return None


class StubSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class StubSessionFactory:
    def __init__(self, db):
        self.db = db

    def __call__(self, *args, **kwargs):
        return StubSessionContext(self.db)


class StubEngine:
    async def dispose(self):
        return None


class StubDB:
    def __init__(self, session=None, user=None, kitchen=None, equipment=None):
        self.session = session
        self.user = user
        self.kitchen = kitchen
        self.equipment = equipment or []

    async def get(self, model_class, pk):
        if model_class is Session:
            return self.session
        if model_class is UserProfile:
            return self.user
        if model_class is KitchenConfig:
            return self.kitchen
        return None

    async def execute(self, stmt):
        result = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = self.equipment
        result.scalars.return_value = scalars
        return result


class _ValidationProbe(BaseModel):
    required_field: int


def _make_validation_error() -> ValidationError:
    try:
        _ValidationProbe.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError("Expected ValidationError")


def _stub_runtime_modules(graph):
    aio_module = types.ModuleType("langgraph.checkpoint.postgres.aio")
    aio_module.AsyncPostgresSaver = StubAsyncPostgresSaver
    graph_module = types.ModuleType("app.graph.graph")
    graph_module.build_grasp_graph = MagicMock(return_value=graph)

    return {
        "langgraph": types.ModuleType("langgraph"),
        "langgraph.checkpoint": types.ModuleType("langgraph.checkpoint"),
        "langgraph.checkpoint.postgres": types.ModuleType("langgraph.checkpoint.postgres"),
        "langgraph.checkpoint.postgres.aio": aio_module,
        "app.graph.graph": graph_module,
    }


def _make_session(user_id: uuid.UUID) -> Session:
    return Session(
        session_id=uuid.uuid4(),
        user_id=user_id,
        status=SessionStatus.PENDING,
        concept_json={
            "free_text": "Dinner party with short ribs.",
            "guest_count": 4,
            "meal_type": "dinner",
            "occasion": "dinner_party",
            "dietary_restrictions": [],
        },
    )


def _make_user() -> UserProfile:
    user_id = uuid.uuid4()
    email = "chef@test.com"
    return UserProfile(
        user_id=user_id,
        name="Test Chef",
        email=email,
        rag_owner_key=UserProfile.build_rag_owner_key(email),
        kitchen_config_id=uuid.uuid4(),
    )


def _make_kitchen(kitchen_config_id: uuid.UUID) -> KitchenConfig:
    return KitchenConfig(kitchen_config_id=kitchen_config_id, max_burners=4, max_oven_racks=2, has_second_oven=False)


def _make_equipment(user_id: uuid.UUID) -> Equipment:
    return Equipment(
        user_id=user_id,
        name="Dutch Oven",
        category=EquipmentCategory.BAKING,
        unlocks_techniques=["braise"],
    )


@pytest.mark.asyncio
async def test_run_pipeline_async_missing_session_returns_without_finalise():
    db = StubDB(session=None, user=None, kitchen=None, equipment=[])
    graph = AsyncMock()
    finalise_session = AsyncMock()
    runtime_modules = _stub_runtime_modules(graph)

    with (
        patch.dict(sys.modules, runtime_modules),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=StubEngine()),
        patch("sqlalchemy.orm.sessionmaker", return_value=StubSessionFactory(db)),
        patch("app.core.status.finalise_session", finalise_session),
    ):
        result = await _run_pipeline_async(str(uuid.uuid4()), str(uuid.uuid4()))

    assert result is None
    finalise_session.assert_not_awaited()
    graph.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_pipeline_async_missing_user_returns_without_finalise():
    session_user_id = uuid.uuid4()
    session = _make_session(session_user_id)
    db = StubDB(session=session, user=None, kitchen=None, equipment=[])
    graph = AsyncMock()
    finalise_session = AsyncMock()
    runtime_modules = _stub_runtime_modules(graph)

    with (
        patch.dict(sys.modules, runtime_modules),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=StubEngine()),
        patch("sqlalchemy.orm.sessionmaker", return_value=StubSessionFactory(db)),
        patch("app.core.status.finalise_session", finalise_session),
    ):
        result = await _run_pipeline_async(str(session.session_id), str(session_user_id))

    assert result is None
    finalise_session.assert_not_awaited()
    graph.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_pipeline_async_validation_error_finalises_failed_session():
    user = _make_user()
    session = _make_session(user.user_id)
    kitchen = _make_kitchen(user.kitchen_config_id)
    equipment = [_make_equipment(user.user_id)]
    db = StubDB(session=session, user=user, kitchen=kitchen, equipment=equipment)
    graph = AsyncMock()
    finalise_session = AsyncMock()
    runtime_modules = _stub_runtime_modules(graph)

    with (
        patch.dict(sys.modules, runtime_modules),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=StubEngine()),
        patch("sqlalchemy.orm.sessionmaker", return_value=StubSessionFactory(db)),
        patch("app.models.pipeline.build_session_initial_state", side_effect=_make_validation_error()),
        patch("app.core.status.finalise_session", finalise_session),
    ):
        await _run_pipeline_async(str(session.session_id), str(user.user_id))

    finalise_session.assert_awaited_once()
    called_session_id, final_state, _db = finalise_session.await_args.args
    assert called_session_id == session.session_id
    assert final_state["schedule"] is None
    assert final_state["errors"][0]["error_type"] == ErrorType.VALIDATION_FAILURE.value
    assert final_state["errors"][0]["node_name"] == "pipeline_startup"
    assert final_state["concept"] == session.concept_json
    graph.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_pipeline_async_graph_exception_records_unknown_error():
    user = _make_user()
    session = _make_session(user.user_id)
    kitchen = _make_kitchen(user.kitchen_config_id)
    equipment = [_make_equipment(user.user_id)]
    db = StubDB(session=session, user=user, kitchen=kitchen, equipment=equipment)
    graph = AsyncMock()
    graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph exploded"))
    finalise_session = AsyncMock()
    runtime_modules = _stub_runtime_modules(graph)
    initial_state = {
        "concept": session.concept_json,
        "kitchen_config": kitchen.model_dump(),
        "equipment": [item.model_dump() for item in equipment],
        "user_id": str(user.user_id),
        "rag_owner_key": user.rag_owner_key,
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
        "errors": [],
    }

    with (
        patch.dict(sys.modules, runtime_modules),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=StubEngine()),
        patch("sqlalchemy.orm.sessionmaker", return_value=StubSessionFactory(db)),
        patch("app.models.pipeline.build_session_initial_state", return_value=(None, initial_state)),
        patch("app.core.status.finalise_session", finalise_session),
    ):
        await _run_pipeline_async(str(session.session_id), str(user.user_id))

    finalise_session.assert_awaited_once()
    called_session_id, final_state, _db = finalise_session.await_args.args
    assert called_session_id == session.session_id
    assert final_state["schedule"] is None
    assert final_state["errors"][0]["error_type"] == "unknown"
    assert final_state["errors"][0]["node_name"] == "celery_task"
    assert final_state["errors"][0]["message"] == "graph exploded"
