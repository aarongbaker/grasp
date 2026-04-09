"""
tests/test_api_routes.py
HTTP-level tests for FastAPI routes using httpx AsyncClient.

Tests cover:
  - Auth: malformed JWT → 401, valid JWT unknown user → 404
  - Sessions: create, run (409 guard, 403 ownership), get status (Fix #7)
  - Authored recipes: create, list, read, cookbook assignment, cross-user denial,
    cookbook ownership, and route-family separation
  - Ingest: PDF-only validation (400), ownership on status poll (Fix #7)

Uses dependency overrides with mock DB session to avoid needing a real
Postgres instance. Tests verify HTTP status codes and request validation.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, select

from app.api.routes.auth import _build_access_token
from app.core.settings import get_settings
from app.models.authored_recipe import AuthoredRecipeRecord, RecipeCookbookRecord
from app.models.recipe import RecipeProvenance
from app.models.enums import ChunkType, IngestionStatus, SessionStatus
from app.models.ingestion import BookRecord, CookbookChunk, IngestionJob
from app.models.session import Session
from app.models.user import BurnerDescriptor, Equipment, KitchenConfig, UserProfile
from tests.conftest import _ensure_test_postgres_available
from tests.fixtures.recipes import ENRICHED_SHORT_RIBS

# ─────────────────────────────────────────────────────────────────────────────
# Test app + fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _create_test_app() -> FastAPI:
    """Create a FastAPI app with routes but no lifespan (no external deps)."""
    from app.api.routes import sessions as sessions_routes
    from app.api.routes.authored_recipes import router as authored_recipes_router
    from app.api.routes.health import router as health_router
    from app.api.routes.ingest import router as ingest_router
    from app.api.routes.recipe_cookbooks import router as recipe_cookbooks_router
    from app.api.routes.sessions import router as sessions_router
    from app.api.routes.users import router as users_router

    app = FastAPI()
    app.state.limiter = sessions_routes.limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded: {exc.detail}"},
        )

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(sessions_router, prefix="/api/v1")
    app.include_router(ingest_router, prefix="/api/v1")
    app.include_router(authored_recipes_router, prefix="/api/v1")
    app.include_router(recipe_cookbooks_router, prefix="/api/v1")
    return app


def _make_test_user() -> UserProfile:
    """Create a UserProfile instance (not persisted — for mock injection)."""
    user = UserProfile(
        user_id=uuid.uuid4(),
        name="Test Chef",
        email="chef@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("chef@test.com"),
        dietary_defaults=["gluten-free"],
    )
    return user


@pytest_asyncio.fixture
async def db_session_for_routes():
    """Fresh async DB session for route-level schema round-trip tests."""
    _ensure_test_postgres_available()
    from app.core.settings import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.test_database_url, echo=False, future=True, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
        await session.rollback()

    await engine.dispose()


class MockDBSession:
    """
    Minimal mock of AsyncSession for route testing.
    Stores objects in memory. Supports add, commit, refresh, get.
    """

    def __init__(self):
        self._store: dict[tuple, object] = {}
        self.exec_result = None
        self.execute_side_effect = None

    def add(self, obj):
        model_class = obj.__class__
        for pk_name in ("session_id", "job_id", "recipe_id", "cookbook_id", "equipment_id", "user_id"):
            if hasattr(obj, pk_name):
                pk = getattr(obj, pk_name)
                if pk is not None:
                    self._store[(model_class, pk)] = obj
                break

    async def commit(self):
        pass

    async def flush(self):
        pass

    async def rollback(self):
        pass

    async def refresh(self, obj):
        # Simulate assigning a UUID if not already set
        if hasattr(obj, "session_id") and obj.session_id is None:
            obj.session_id = uuid.uuid4()
        if hasattr(obj, "job_id") and obj.job_id is None:
            obj.job_id = uuid.uuid4()
        if hasattr(obj, "recipe_id") and obj.recipe_id is None:
            obj.recipe_id = uuid.uuid4()
        if hasattr(obj, "equipment_id") and obj.equipment_id is None:
            obj.equipment_id = uuid.uuid4()
        if isinstance(obj, RecipeCookbookRecord) and obj.cookbook_id is None:
            obj.cookbook_id = uuid.uuid4()
        if hasattr(obj, "updated_at"):
            obj.updated_at = getattr(obj, "updated_at") or datetime.now(timezone.utc).replace(tzinfo=None)
        self.add(obj)

    async def get(self, model_class, pk):
        return self._store.get((model_class, pk))

    def seed(self, model_class, pk, obj):
        """Pre-populate a row for get() to find."""
        self._store[(model_class, pk)] = obj

    async def execute(self, stmt):
        if self.execute_side_effect is not None:
            raise self.execute_side_effect
        return MagicMock()

    async def delete(self, obj):
        model_class = obj.__class__
        for pk_name in ("session_id", "job_id", "recipe_id", "cookbook_id", "equipment_id", "user_id"):
            if hasattr(obj, pk_name):
                pk = getattr(obj, pk_name)
                self._store.pop((model_class, pk), None)
                break

    async def exec(self, stmt):
        """Stub for select queries."""
        if self.exec_result is not None:
            return self.exec_result

        statement_text = str(stmt)
        lowered_text = statement_text.lower()
        where_criteria = getattr(stmt, "_where_criteria", ())

        def _extract_uuid_filter():
            for criterion in where_criteria:
                right = getattr(criterion, "right", None)
                value = getattr(right, "value", None)
                if isinstance(value, uuid.UUID):
                    return value
            return None

        def _extract_like_filter():
            for criterion in where_criteria:
                right = getattr(criterion, "right", None)
                value = getattr(right, "value", None)
                if isinstance(value, str) and "%" in value:
                    return value.strip("%").lower()
            return None

        def _extract_uuid_filters():
            filters = {}
            for criterion in where_criteria:
                left = getattr(criterion, "left", None)
                right = getattr(criterion, "right", None)
                column_name = getattr(left, "key", None)
                value = getattr(right, "value", None)
                if column_name and isinstance(value, uuid.UUID):
                    filters[column_name] = value
            return filters

        if "from authored_recipes" in lowered_text:
            user_id = _extract_uuid_filter()
            name_filter = _extract_like_filter()
            rows = [
                obj
                for (model_class, _), obj in self._store.items()
                if model_class is AuthoredRecipeRecord
                and (user_id is None or obj.user_id == user_id)
                and (name_filter is None or name_filter in obj.title.lower())
            ]
            rows.sort(key=lambda record: record.updated_at, reverse=True)
            mock_result = MagicMock()
            mock_result.first.return_value = rows[0] if rows else None
            mock_result.all.return_value = rows
            return mock_result

        if "from recipe_cookbooks" in lowered_text:
            user_id = _extract_uuid_filter()
            name_filter = _extract_like_filter()
            rows = [
                obj
                for (model_class, _), obj in self._store.items()
                if model_class is RecipeCookbookRecord
                and (user_id is None or obj.user_id == user_id)
                and (name_filter is None or name_filter in obj.name.lower())
            ]
            rows.sort(key=lambda record: (record.updated_at, record.name), reverse=True)
            mock_result = MagicMock()
            mock_result.first.return_value = rows[0] if rows else None
            mock_result.all.return_value = rows
            return mock_result

        if "from equipment" in lowered_text:
            uuid_filters = _extract_uuid_filters()
            rows = [
                obj
                for (model_class, _), obj in self._store.items()
                if model_class is Equipment
                and all(getattr(obj, field_name) == value for field_name, value in uuid_filters.items())
            ]
            mock_result = MagicMock()
            mock_result.first.return_value = rows[0] if rows else None
            mock_result.all.return_value = rows
            return mock_result

        if "from sessions" in lowered_text:
            uuid_filters = _extract_uuid_filters()
            rows = [
                obj
                for (model_class, _), obj in self._store.items()
                if model_class is Session
                and all(getattr(obj, field_name) == value for field_name, value in uuid_filters.items())
            ]
            mock_result = MagicMock()
            mock_result.first.return_value = rows[0] if rows else None
            mock_result.all.return_value = rows
            return mock_result

        if "from user_profiles" in lowered_text:
            uuid_filters = _extract_uuid_filters()
            rows = [
                obj
                for (model_class, _), obj in self._store.items()
                if model_class is UserProfile
                and all(getattr(obj, field_name) == value for field_name, value in uuid_filters.items())
            ]
            mock_result = MagicMock()
            mock_result.first.return_value = rows[0] if rows else None
            mock_result.all.return_value = rows
            return mock_result

        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_result.all.return_value = []
        return mock_result


@pytest.fixture
def test_user():
    return _make_test_user()


@pytest.fixture
def mock_db():
    return MockDBSession()


@pytest.fixture
def app_with_overrides(mock_db, test_user):
    """App with auth + DB overridden."""
    from app.core.auth import get_current_user
    from app.db.session import get_session

    app = _create_test_app()

    async def _override_session():
        yield mock_db

    async def _override_user():
        return test_user

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = _override_user
    return app


@pytest.fixture
def authored_recipe_payload(test_user):
    return {
        "user_id": str(test_user.user_id),
        "title": "Roasted Carrots with Labneh",
        "description": "A plated vegetable course with warm spices.",
        "cuisine": "Levantine",
        "yield_info": {"quantity": 4, "unit": "plates", "notes": "starter portions"},
        "ingredients": [
            {"name": "carrots", "quantity": "2 lb"},
            {"name": "labneh", "quantity": "1 cup"},
        ],
        "steps": [
            {
                "title": "Roast carrots",
                "instruction": "Roast until caramelized.",
                "duration_minutes": 35,
                "resource": "oven",
                "required_equipment": ["sheet tray"],
            },
            {
                "title": "Plate",
                "instruction": "Spread labneh and arrange carrots.",
                "duration_minutes": 10,
                "resource": "hands",
                "dependencies": [
                    {
                        "step_id": "roasted_carrots_with_labneh_step_1",
                        "kind": "finish_to_start",
                        "lag_minutes": 0,
                    }
                ],
            },
        ],
        "equipment_notes": ["Needs one full sheet tray."],
        "storage": {"method": "refrigerated", "duration": "2 days", "notes": "store components separately"},
        "hold": {"method": "warming drawer", "max_duration": "15 minutes", "notes": "avoid overdrying"},
        "reheat": {"method": "oven", "target": "hot through", "notes": "refresh with olive oil"},
        "make_ahead_guidance": "Roast carrots in the afternoon, then rewarm before service.",
        "plating_notes": "Finish with pistachio dukkah.",
        "chef_notes": "Keep the labneh cold for contrast.",
    }


@pytest.fixture(autouse=True)
def reset_session_route_limiter():
    from app.api.routes.sessions import limiter as sessions_limiter

    sessions_limiter._storage.reset()
    yield
    sessions_limiter._storage.reset()


def _auth_headers_for(user: UserProfile) -> dict[str, str]:
    token, _expires_in = _build_access_token(str(user.user_id), user.email, get_settings())
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
# Kitchen config burner descriptor support
# ─────────────────────────────────────────────────────────────────────────────


async def test_update_kitchen_accepts_optional_burner_descriptors(app_with_overrides, test_user, mock_db):
    kitchen = KitchenConfig(
        kitchen_config_id=uuid.uuid4(),
        max_burners=4,
        max_oven_racks=2,
        has_second_oven=False,
        burners=[],
    )
    test_user.kitchen_config_id = kitchen.kitchen_config_id
    mock_db.exec_result = MagicMock()
    mock_db.exec_result.first.return_value = kitchen

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            f"/api/v1/users/{test_user.user_id}/kitchen",
            json={
                "burners": [
                    {
                        "burner_id": "front_left_large",
                        "position": "front_left",
                        "size": "large",
                        "label": "Front Left",
                    }
                ]
            },
        )

    assert response.status_code == 200
    assert response.json()["burners"] == [
        {
            "burner_id": "front_left_large",
            "position": "front_left",
            "size": "large",
            "label": "Front Left",
        }
    ]
    assert kitchen.burners == [
        BurnerDescriptor(
            burner_id="front_left_large",
            position="front_left",
            size="large",
            label="Front Left",
        )
    ]


async def test_update_kitchen_rejects_burner_capacity_above_locked_ceiling(app_with_overrides, test_user):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            f"/api/v1/users/{test_user.user_id}/kitchen",
            json={"max_burners": 11},
        )

    assert response.status_code == 422


async def test_update_kitchen_rejects_more_burners_than_capacity(app_with_overrides, test_user, mock_db):
    kitchen = KitchenConfig(
        kitchen_config_id=uuid.uuid4(),
        max_burners=2,
        max_oven_racks=2,
        has_second_oven=False,
        burners=[],
    )
    test_user.kitchen_config_id = kitchen.kitchen_config_id
    mock_db.exec_result = MagicMock()
    mock_db.exec_result.first.return_value = kitchen

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            f"/api/v1/users/{test_user.user_id}/kitchen",
            json={
                "max_burners": 1,
                "burners": [
                    {"burner_id": "front_left_large"},
                    {"burner_id": "front_right_medium"},
                ],
            },
        )

    assert response.status_code == 422
    assert "burners count cannot exceed max_burners" in str(response.json()["detail"])


async def test_update_kitchen_rejects_second_oven_racks_without_second_oven(app_with_overrides, test_user):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            f"/api/v1/users/{test_user.user_id}/kitchen",
            json={
                "has_second_oven": False,
                "max_second_oven_racks": 2,
            },
        )

    assert response.status_code == 422
    assert "max_second_oven_racks requires has_second_oven=true" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_kitchen_config_burners_round_trip_through_real_db(db_session_for_routes):
    kitchen = KitchenConfig(
        max_burners=4,
        max_oven_racks=2,
        has_second_oven=False,
        max_second_oven_racks=2,
        burners=[
            BurnerDescriptor(
                burner_id="front_left_large",
                position="front_left",
                size="large",
                label="Front Left",
            )
        ],
    )
    db_session_for_routes.add(kitchen)
    await db_session_for_routes.commit()

    result = await db_session_for_routes.exec(
        select(KitchenConfig).where(KitchenConfig.kitchen_config_id == kitchen.kitchen_config_id)
    )
    stored = result.first()

    assert stored is not None
    assert [burner.model_dump() for burner in stored.burners] == [
        {
            "burner_id": "front_left_large",
            "position": "front_left",
            "size": "large",
            "label": "Front Left",
        }
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Auth edge cases
# ─────────────────────────────────────────────────────────────────────────────


async def test_health_check_returns_ok_with_connected_db(app_with_overrides):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "connected"}


async def test_health_check_returns_500_when_db_execute_fails(mock_db):
    from app.db.session import get_session

    app = _create_test_app()
    mock_db.execute_side_effect = RuntimeError("database unavailable")

    async def _override_session():
        yield mock_db

    app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/health")

    assert resp.status_code == 500
    app.dependency_overrides.clear()


async def test_auth_invalid_token_returns_401():
    """Malformed JWT token should return 401."""
    from app.db.session import get_session

    app = _create_test_app()
    mock_db = MockDBSession()

    async def _override_session():
        yield mock_db

    app.dependency_overrides[get_session] = _override_session
    # Do NOT override get_current_user — let the real auth run
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/sessions/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": "Bearer not-a-valid-token"},
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing or invalid authentication token."
    app.dependency_overrides.clear()


async def test_auth_valid_token_unknown_user_returns_404():
    """Valid JWT for nonexistent user should return 404."""
    from datetime import timedelta

    import jwt as pyjwt

    from app.core.settings import get_settings
    from app.db.session import get_session

    settings = get_settings()
    fake_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {"sub": fake_id, "iat": now, "exp": now + timedelta(hours=1)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    app = _create_test_app()
    mock_db = MockDBSession()

    async def _override_session():
        yield mock_db

    app.dependency_overrides[get_session] = _override_session
    # Real auth runs — mock_db.exec returns None for select query
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/sessions/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Session routes (Fix #7)
# ─────────────────────────────────────────────────────────────────────────────


async def test_add_equipment_returns_201_and_persists_row(app_with_overrides, test_user, mock_db):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/users/{test_user.user_id}/equipment",
            json={
                "name": "Stand Mixer",
                "category": "prep",
                "unlocks_techniques": ["laminated_dough", "meringue"],
            },
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["equipment_id"]
    assert data["user_id"] == str(test_user.user_id)
    assert data["name"] == "Stand Mixer"
    assert data["category"] == "prep"
    assert data["unlocks_techniques"] == ["laminated_dough", "meringue"]

    result = await mock_db.exec(
        select(Equipment).where(Equipment.equipment_id == uuid.UUID(data["equipment_id"]), Equipment.user_id == test_user.user_id)
    )
    equipment = result.first()
    assert equipment is not None
    assert equipment.name == "Stand Mixer"
    assert equipment.unlocks_techniques == ["laminated_dough", "meringue"]


async def test_add_equipment_rejects_more_than_twenty_items(app_with_overrides, test_user, mock_db):
    for index in range(20):
        equipment = Equipment(
            equipment_id=uuid.uuid4(),
            user_id=test_user.user_id,
            name=f"Equipment {index}",
            category="prep",
            unlocks_techniques=[],
        )
        mock_db.seed(Equipment, equipment.equipment_id, equipment)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/users/{test_user.user_id}/equipment",
            json={
                "name": "Overflow Mixer",
                "category": "prep",
                "unlocks_techniques": ["laminated_dough"],
            },
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Equipment limit exceeded: maximum 20 items"


async def test_delete_equipment_returns_204_and_removes_row(app_with_overrides, test_user, mock_db):
    equipment = Equipment(
        equipment_id=uuid.uuid4(),
        user_id=test_user.user_id,
        name="Dutch Oven",
        category="baking",
        unlocks_techniques=["braise"],
    )
    mock_db.seed(Equipment, equipment.equipment_id, equipment)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.delete(f"/api/v1/users/{test_user.user_id}/equipment/{equipment.equipment_id}")

    assert resp.status_code == 204

    result = await mock_db.exec(
        select(Equipment).where(Equipment.equipment_id == equipment.equipment_id, Equipment.user_id == test_user.user_id)
    )
    assert result.first() is None


async def test_delete_equipment_returns_403_for_cross_user_caller(app_with_overrides, mock_db, test_user):
    from app.core.auth import get_current_user

    equipment = Equipment(
        equipment_id=uuid.uuid4(),
        user_id=test_user.user_id,
        name="Sheet Pan",
        category="baking",
        unlocks_techniques=["roast"],
    )
    mock_db.seed(Equipment, equipment.equipment_id, equipment)

    other_user = UserProfile(
        user_id=uuid.uuid4(),
        name="Other Chef",
        email="other@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("other@test.com"),
    )

    async def _override_other_user():
        return other_user

    app_with_overrides.dependency_overrides[get_current_user] = _override_other_user

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.delete(f"/api/v1/users/{test_user.user_id}/equipment/{equipment.equipment_id}")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


async def test_delete_equipment_returns_404_when_row_missing(app_with_overrides, test_user):
    missing_equipment_id = uuid.uuid4()

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.delete(f"/api/v1/users/{test_user.user_id}/equipment/{missing_equipment_id}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Equipment not found"


async def test_create_session_201(app_with_overrides, test_user):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "free_text": "Dinner party with lamb and rosemary.",
                "guest_count": 4,
                "dish_count": 4,
                "meal_type": "dinner",
                "occasion": "dinner_party",
                "dietary_restrictions": ["nut-free"],
            },
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    # dietary_defaults merged: gluten-free from user + nut-free from request
    concept = data["concept_json"]
    assert concept["concept_source"] == "free_text"
    assert concept["dish_count"] == 4
    assert concept["selected_authored_recipe"] is None
    assert "gluten-free" in concept["dietary_restrictions"]
    assert "nut-free" in concept["dietary_restrictions"]


@pytest.mark.asyncio
async def test_create_session_rate_limit_isolated_by_authenticated_user(mock_db):
    from app.db.session import get_session

    app = _create_test_app()
    first_user = _make_test_user()
    second_user = _make_test_user()
    mock_db.seed(UserProfile, first_user.user_id, first_user)
    mock_db.seed(UserProfile, second_user.user_id, second_user)

    async def _override_session():
        yield mock_db

    app.dependency_overrides[get_session] = _override_session

    payload = {
        "free_text": "Dinner party with lamb and rosemary.",
        "guest_count": 4,
        "meal_type": "dinner",
        "occasion": "dinner_party",
        "dietary_restrictions": ["nut-free"],
    }
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        for _ in range(10):
            resp = await ac.post("/api/v1/sessions", json=payload, headers=_auth_headers_for(first_user))
            assert resp.status_code == 201

        limited = await ac.post("/api/v1/sessions", json=payload, headers=_auth_headers_for(first_user))
        assert limited.status_code == 429
        assert "Rate limit exceeded" in limited.json()["detail"]

        other_user = await ac.post("/api/v1/sessions", json=payload, headers=_auth_headers_for(second_user))
        assert other_user.status_code == 201

    app.dependency_overrides.clear()


async def test_create_session_with_authored_recipe_201(app_with_overrides, mock_db, test_user):
    authored_recipe = AuthoredRecipeRecord(
        user_id=test_user.user_id,
        title="Braised Fennel with Saffron",
        description="Private library draft.",
        cuisine="Mediterranean",
        authored_payload={
            "title": "Braised Fennel with Saffron",
            "description": "Private library draft.",
            "cuisine": "Mediterranean",
            "yield_info": {"quantity": 4, "unit": "plates"},
            "ingredients": [{"name": "fennel", "quantity": "4 bulbs"}],
            "steps": [
                {
                    "title": "Braise",
                    "instruction": "Cook gently until tender.",
                    "duration_minutes": 30,
                    "resource": "stovetop",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    mock_db.seed(AuthoredRecipeRecord, authored_recipe.recipe_id, authored_recipe)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "authored",
                "free_text": "Service tonight from my library.",
                "selected_authored_recipe": {
                    "recipe_id": str(authored_recipe.recipe_id),
                    "title": "Client supplied title should be ignored",
                },
                "guest_count": 4,
                "meal_type": "dinner",
                "occasion": "casual",
                "dietary_restrictions": ["nut-free"],
            },
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    concept = data["concept_json"]
    assert concept["concept_source"] == "authored"
    assert concept["selected_recipes"] == []
    assert concept["selected_authored_recipe"] == {
        "recipe_id": str(authored_recipe.recipe_id),
        "title": authored_recipe.title,
    }
    assert "gluten-free" in concept["dietary_restrictions"]
    assert "nut-free" in concept["dietary_restrictions"]


async def test_create_session_with_planner_authored_anchor_201(app_with_overrides, mock_db, test_user):
    authored_recipe = AuthoredRecipeRecord(
        user_id=test_user.user_id,
        title="Braised Fennel with Saffron",
        description="Private library draft.",
        cuisine="Mediterranean",
        authored_payload={
            "title": "Braised Fennel with Saffron",
            "description": "Private library draft.",
            "cuisine": "Mediterranean",
            "yield_info": {"quantity": 4, "unit": "plates"},
            "ingredients": [{"name": "fennel", "quantity": "4 bulbs"}],
            "steps": [
                {
                    "title": "Braise",
                    "instruction": "Cook gently until tender.",
                    "duration_minutes": 30,
                    "resource": "stovetop",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    mock_db.seed(AuthoredRecipeRecord, authored_recipe.recipe_id, authored_recipe)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "planner_authored_anchor",
                "free_text": "Plan around this authored anchor.",
                "planner_authored_recipe_anchor": {
                    "recipe_id": str(authored_recipe.recipe_id),
                    "title": "Client supplied title should be ignored",
                },
                "guest_count": 4,
                "meal_type": "dinner",
                "occasion": "casual",
                "dietary_restrictions": ["nut-free"],
            },
        )

    assert resp.status_code == 201
    data = resp.json()
    concept = data["concept_json"]
    assert concept["concept_source"] == "planner_authored_anchor"
    assert concept["selected_recipes"] == []
    assert concept["selected_authored_recipe"] is None
    assert concept["planner_cookbook_target"] is None
    assert concept["planner_authored_recipe_anchor"] == {
        "recipe_id": str(authored_recipe.recipe_id),
        "title": authored_recipe.title,
    }
    assert "gluten-free" in concept["dietary_restrictions"]
    assert "nut-free" in concept["dietary_restrictions"]


async def test_create_session_with_planner_cookbook_target_201(app_with_overrides, mock_db, test_user):
    cookbook = RecipeCookbookRecord(
        user_id=test_user.user_id,
        name="Weeknight Menus",
        description="Planner-facing authored containers.",
    )
    mock_db.seed(RecipeCookbookRecord, cookbook.cookbook_id, cookbook)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "planner_cookbook_target",
                "free_text": "Plan within this cookbook container.",
                "planner_cookbook_target": {
                    "cookbook_id": str(cookbook.cookbook_id),
                    "name": "Client supplied name should be ignored",
                    "mode": "cookbook_biased",
                },
                "guest_count": 6,
                "meal_type": "dinner",
                "occasion": "dinner_party",
                "dietary_restrictions": [],
            },
        )

    assert resp.status_code == 201
    data = resp.json()
    concept = data["concept_json"]
    assert concept["concept_source"] == "planner_cookbook_target"
    assert concept["selected_recipes"] == []
    assert concept["selected_authored_recipe"] is None
    assert concept["planner_authored_recipe_anchor"] is None
    assert concept["planner_cookbook_target"] == {
        "cookbook_id": str(cookbook.cookbook_id),
        "name": cookbook.name,
        "description": cookbook.description,
        "mode": "cookbook_biased",
    }


async def test_resolve_planner_authored_reference_returns_resolved_match(app_with_overrides, mock_db, test_user):
    authored_recipe = AuthoredRecipeRecord(
        user_id=test_user.user_id,
        title="Sunday Braise",
        description="Anchoring dish.",
        cuisine="French",
        authored_payload={
            "title": "Sunday Braise",
            "description": "Anchoring dish.",
            "cuisine": "French",
            "yield_info": {"quantity": 4, "unit": "plates"},
            "ingredients": [{"name": "beef", "quantity": "2 lb"}],
            "steps": [
                {
                    "title": "Braise",
                    "instruction": "Cook gently until tender.",
                    "duration_minutes": 120,
                    "resource": "stovetop",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    foreign_recipe = AuthoredRecipeRecord(
        user_id=uuid.uuid4(),
        title="Sunday Braise",
        description="Should not leak.",
        cuisine="French",
        authored_payload=authored_recipe.authored_payload,
    )
    mock_db.seed(AuthoredRecipeRecord, authored_recipe.recipe_id, authored_recipe)
    mock_db.seed(AuthoredRecipeRecord, foreign_recipe.recipe_id, foreign_recipe)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions/planner/resolve",
            json={"kind": "authored", "reference": " sunday braise "},
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "kind": "authored",
        "reference": "sunday braise",
        "status": "resolved",
        "matches": [
            {
                "kind": "authored",
                "recipe_id": str(authored_recipe.recipe_id),
                "title": authored_recipe.title,
            }
        ],
    }


async def test_resolve_planner_cookbook_reference_returns_ambiguous_matches(app_with_overrides, mock_db, test_user):
    first = RecipeCookbookRecord(
        user_id=test_user.user_id,
        name="Desserts",
        description="Plated desserts.",
    )
    second = RecipeCookbookRecord(
        user_id=test_user.user_id,
        name="Frozen Desserts",
        description="Ice cream service.",
    )
    foreign = RecipeCookbookRecord(
        user_id=uuid.uuid4(),
        name="Desserts",
        description="Should not leak.",
    )
    mock_db.seed(RecipeCookbookRecord, first.cookbook_id, first)
    mock_db.seed(RecipeCookbookRecord, second.cookbook_id, second)
    mock_db.seed(RecipeCookbookRecord, foreign.cookbook_id, foreign)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions/planner/resolve",
            json={"kind": "cookbook", "reference": "desserts"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "cookbook"
    assert data["reference"] == "desserts"
    assert data["status"] == "ambiguous"
    assert {match["cookbook_id"] for match in data["matches"]} == {
        str(first.cookbook_id),
        str(second.cookbook_id),
    }
    assert {match["name"] for match in data["matches"]} == {first.name, second.name}
    assert all(match["kind"] == "cookbook" for match in data["matches"])
    assert all(match["description"] in {first.description, second.description} for match in data["matches"])


async def test_resolve_planner_reference_returns_no_match(app_with_overrides, mock_db, test_user):
    cookbook = RecipeCookbookRecord(
        user_id=test_user.user_id,
        name="Vegetables",
        description="Produce-forward.",
    )
    mock_db.seed(RecipeCookbookRecord, cookbook.cookbook_id, cookbook)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions/planner/resolve",
            json={"kind": "cookbook", "reference": "desserts"},
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "kind": "cookbook",
        "reference": "desserts",
        "status": "no_match",
        "matches": [],
    }


async def test_create_session_with_authored_recipe_requires_ownership(app_with_overrides, mock_db):
    authored_recipe = AuthoredRecipeRecord(
        user_id=uuid.uuid4(),
        title="Private Braise",
        description="Owned by someone else.",
        cuisine="French",
        authored_payload={
            "title": "Private Braise",
            "description": "Owned by someone else.",
            "cuisine": "French",
            "yield_info": {"quantity": 2, "unit": "plates"},
            "ingredients": [{"name": "fennel", "quantity": "2 bulbs"}],
            "steps": [
                {
                    "title": "Cook",
                    "instruction": "Keep private.",
                    "duration_minutes": 20,
                    "resource": "stovetop",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    mock_db.seed(AuthoredRecipeRecord, authored_recipe.recipe_id, authored_recipe)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "authored",
                "free_text": "Schedule my private-library recipe.",
                "selected_authored_recipe": {
                    "recipe_id": str(authored_recipe.recipe_id),
                    "title": authored_recipe.title,
                },
                "guest_count": 2,
                "meal_type": "dinner",
                "occasion": "casual",
                "dietary_restrictions": [],
            },
        )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


async def test_create_session_with_planner_authored_anchor_requires_ownership(app_with_overrides, mock_db):
    authored_recipe = AuthoredRecipeRecord(
        user_id=uuid.uuid4(),
        title="Private Braise",
        description="Owned by someone else.",
        cuisine="French",
        authored_payload={
            "title": "Private Braise",
            "description": "Owned by someone else.",
            "cuisine": "French",
            "yield_info": {"quantity": 2, "unit": "plates"},
            "ingredients": [{"name": "fennel", "quantity": "2 bulbs"}],
            "steps": [
                {
                    "title": "Cook",
                    "instruction": "Keep private.",
                    "duration_minutes": 20,
                    "resource": "stovetop",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    mock_db.seed(AuthoredRecipeRecord, authored_recipe.recipe_id, authored_recipe)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "planner_authored_anchor",
                "free_text": "Plan around my private-library recipe.",
                "planner_authored_recipe_anchor": {
                    "recipe_id": str(authored_recipe.recipe_id),
                    "title": authored_recipe.title,
                },
                "guest_count": 2,
                "meal_type": "dinner",
                "occasion": "casual",
                "dietary_restrictions": [],
            },
        )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


async def test_create_session_with_planner_cookbook_target_requires_ownership(app_with_overrides, mock_db):
    cookbook = RecipeCookbookRecord(
        user_id=uuid.uuid4(),
        name="Private Container",
        description="Owned by someone else.",
    )
    mock_db.seed(RecipeCookbookRecord, cookbook.cookbook_id, cookbook)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "planner_cookbook_target",
                "free_text": "Plan within my private cookbook.",
                "planner_cookbook_target": {
                    "cookbook_id": str(cookbook.cookbook_id),
                    "name": cookbook.name,
                    "mode": "strict",
                },
                "guest_count": 2,
                "meal_type": "dinner",
                "occasion": "casual",
                "dietary_restrictions": [],
            },
        )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


async def test_create_session_with_authored_recipe_rejects_missing_recipe(app_with_overrides):
    missing_recipe_id = uuid.uuid4()

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "authored",
                "free_text": "Schedule my private-library recipe.",
                "selected_authored_recipe": {
                    "recipe_id": str(missing_recipe_id),
                    "title": "Missing Recipe",
                },
                "guest_count": 2,
                "meal_type": "dinner",
                "occasion": "casual",
                "dietary_restrictions": [],
            },
        )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Authored recipe not found"


async def test_create_session_with_planner_cookbook_target_rejects_missing_cookbook(app_with_overrides):
    missing_cookbook_id = uuid.uuid4()

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "planner_cookbook_target",
                "free_text": "Plan within this cookbook container.",
                "planner_cookbook_target": {
                    "cookbook_id": str(missing_cookbook_id),
                    "name": "Missing Cookbook",
                    "mode": "strict",
                },
                "guest_count": 4,
                "meal_type": "dinner",
                "occasion": "casual",
                "dietary_restrictions": [],
            },
        )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Recipe cookbook not found"


async def test_create_session_rejects_mixed_planner_authored_anchor_shape(app_with_overrides, mock_db, test_user):
    authored_recipe = AuthoredRecipeRecord(
        user_id=test_user.user_id,
        title="Braised Fennel with Saffron",
        description="Private library draft.",
        cuisine="Mediterranean",
        authored_payload={
            "title": "Braised Fennel with Saffron",
            "description": "Private library draft.",
            "cuisine": "Mediterranean",
            "yield_info": {"quantity": 4, "unit": "plates"},
            "ingredients": [{"name": "fennel", "quantity": "4 bulbs"}],
            "steps": [
                {
                    "title": "Braise",
                    "instruction": "Cook gently until tender.",
                    "duration_minutes": 30,
                    "resource": "stovetop",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    mock_db.seed(AuthoredRecipeRecord, authored_recipe.recipe_id, authored_recipe)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "planner_authored_anchor",
                "free_text": "Plan around this authored anchor.",
                "planner_authored_recipe_anchor": {
                    "recipe_id": str(authored_recipe.recipe_id),
                    "title": authored_recipe.title,
                },
                "planner_cookbook_target": {
                    "cookbook_id": str(uuid.uuid4()),
                    "name": "Should not be allowed",
                },
                "guest_count": 2,
                "meal_type": "dinner",
                "occasion": "casual",
                "dietary_restrictions": [],
            },
        )

    assert resp.status_code == 422
    data = resp.json()
    assert isinstance(data["detail"], list)
    assert any("planner_cookbook_target" in issue["loc"] for issue in data["detail"])


async def test_create_session_rejects_mixed_authored_shape(app_with_overrides, mock_db, test_user):
    authored_recipe = AuthoredRecipeRecord(
        user_id=test_user.user_id,
        title="Braised Fennel with Saffron",
        description="Private library draft.",
        cuisine="Mediterranean",
        authored_payload={
            "title": "Braised Fennel with Saffron",
            "description": "Private library draft.",
            "cuisine": "Mediterranean",
            "yield_info": {"quantity": 4, "unit": "plates"},
            "ingredients": [{"name": "fennel", "quantity": "4 bulbs"}],
            "steps": [
                {
                    "title": "Braise",
                    "instruction": "Cook gently until tender.",
                    "duration_minutes": 30,
                    "resource": "stovetop",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    mock_db.seed(AuthoredRecipeRecord, authored_recipe.recipe_id, authored_recipe)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "authored",
                "free_text": "Schedule my private-library recipe.",
                "selected_authored_recipe": {
                    "recipe_id": str(authored_recipe.recipe_id),
                    "title": authored_recipe.title,
                },
                "selected_recipes": [{"chunk_id": str(uuid.uuid4())}],
                "guest_count": 2,
                "meal_type": "dinner",
                "occasion": "casual",
                "dietary_restrictions": [],
            },
        )

    assert resp.status_code == 422
    data = resp.json()
    assert isinstance(data["detail"], list)
    assert any("selected_recipes" in issue["loc"] for issue in data["detail"])


async def test_run_pipeline_requires_ownership(app_with_overrides, mock_db):
    session = Session(user_id=uuid.uuid4(), status=SessionStatus.PENDING, concept_json={})
    mock_db.seed(Session, session.session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(f"/api/v1/sessions/{session.session_id}/run")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


async def test_run_pipeline_rejects_non_pending_session(app_with_overrides, mock_db, test_user):
    session = Session(user_id=test_user.user_id, status=SessionStatus.GENERATING, concept_json={})
    mock_db.seed(Session, session.session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(f"/api/v1/sessions/{session.session_id}/run")

    assert resp.status_code == 409
    assert "already" in resp.json()["detail"]


async def test_cancel_pipeline_returns_cancelled_for_already_cancelled_session(app_with_overrides, mock_db, test_user):
    session = Session(user_id=test_user.user_id, status=SessionStatus.CANCELLED, concept_json={})
    mock_db.seed(Session, session.session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(f"/api/v1/sessions/{session.session_id}/cancel")

    assert resp.status_code == 200
    assert resp.json() == {"session_id": str(session.session_id), "status": "cancelled"}


async def test_cancel_pipeline_rejects_terminal_complete_session(app_with_overrides, mock_db, test_user):
    session = Session(user_id=test_user.user_id, status=SessionStatus.COMPLETE, concept_json={})
    mock_db.seed(Session, session.session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(f"/api/v1/sessions/{session.session_id}/cancel")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Session is complete, not in progress"


async def test_get_session_status_requires_ownership(app_with_overrides, mock_db):
    session = Session(user_id=uuid.uuid4(), status=SessionStatus.PENDING, concept_json={})
    mock_db.seed(Session, session.session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/sessions/{session.session_id}")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


async def test_get_session_results_fast_path_returns_persisted_provenance(app_with_overrides, mock_db, test_user):
    validated_recipe = {
        "source": {
            **ENRICHED_SHORT_RIBS.model_dump(mode="json"),
            "source": {
                **ENRICHED_SHORT_RIBS.source.model_dump(mode="json"),
                "provenance": {
                    "kind": "library_authored",
                    "source_label": "Sunday Braise",
                    "recipe_id": str(uuid.uuid4()),
                    "cookbook_id": str(uuid.uuid4()),
                },
            },
            "rag_sources": ["chunk_001"],
        },
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "warnings": [],
        "passed": True,
    }
    session = Session(
        user_id=test_user.user_id,
        status=SessionStatus.COMPLETE,
        concept_json={},
        result_schedule={"summary": "Ready", "timeline": [], "total_duration_minutes": 10, "error_summary": None},
        result_recipes=[validated_recipe],
    )
    mock_db.seed(Session, session.session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/sessions/{session.session_id}/results")

    assert resp.status_code == 200
    data = resp.json()
    assert data["errors"] == []
    assert data["schedule"]["total_duration_minutes_max"] is None
    assert data["schedule"]["one_oven_conflict"] == {
        "classification": "compatible",
        "tolerance_f": 15,
        "has_second_oven": False,
        "temperature_gap_f": None,
        "blocking_recipe_names": [],
        "affected_step_ids": [],
        "remediation": {
            "requires_resequencing": False,
            "suggested_actions": [],
            "delaying_recipe_names": [],
            "blocking_recipe_names": [],
            "notes": None,
        },
    }
    assert data["recipes"][0]["source"]["source"]["provenance"] == validated_recipe["source"]["source"]["provenance"]
    assert data["recipes"][0]["source"]["rag_sources"] == ["chunk_001"]


async def test_get_session_results_fallback_returns_checkpoint_provenance(app_with_overrides, mock_db, test_user):
    generated_recipe_id = str(uuid.uuid4())
    session = Session(
        user_id=test_user.user_id,
        status=SessionStatus.COMPLETE,
        concept_json={},
        result_schedule=None,
        result_recipes=None,
    )
    mock_db.seed(Session, session.session_id, session)

    checkpoint_recipe = {
        "source": {
            **ENRICHED_SHORT_RIBS.model_dump(mode="json"),
            "source": {
                **ENRICHED_SHORT_RIBS.source.model_dump(mode="json"),
                "provenance": {
                    "kind": "library_cookbook",
                    "source_label": "Market Suppers",
                    "recipe_id": None,
                    "cookbook_id": generated_recipe_id,
                },
            },
            "rag_sources": [],
        },
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "warnings": [],
        "passed": True,
    }
    mock_snapshot = MagicMock()
    mock_snapshot.values = {
        "schedule": {"summary": "Ready", "timeline": [], "total_duration_minutes": 10, "error_summary": None},
        "validated_recipes": [checkpoint_recipe],
        "errors": [{"node_name": "validator", "message": "kept for parity", "recoverable": True}],
    }
    mock_graph = AsyncMock()
    mock_graph.aget_state.return_value = mock_snapshot

    with patch("app.main.get_graph", new=AsyncMock(return_value=mock_graph)):
        transport = ASGITransport(app=app_with_overrides)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/sessions/{session.session_id}/results")

    assert resp.status_code == 200
    data = resp.json()
    assert data["schedule"]["total_duration_minutes_max"] is None
    assert data["schedule"]["one_oven_conflict"] == {
        "classification": "compatible",
        "tolerance_f": 15,
        "has_second_oven": False,
        "temperature_gap_f": None,
        "blocking_recipe_names": [],
        "affected_step_ids": [],
        "remediation": {
            "requires_resequencing": False,
            "suggested_actions": [],
            "delaying_recipe_names": [],
            "blocking_recipe_names": [],
            "notes": None,
        },
    }
    assert data["recipes"][0]["source"]["source"]["provenance"] == checkpoint_recipe["source"]["source"]["provenance"]
    assert data["recipes"][0]["source"]["rag_sources"] == []
    assert data["errors"] == [{"node_name": "validator", "message": "kept for parity", "recoverable": True}]
async def test_get_session_results_fast_path_preserves_explicit_schedule_metadata(app_with_overrides, mock_db, test_user):
    session = Session(
        user_id=test_user.user_id,
        status=SessionStatus.COMPLETE,
        concept_json={},
        result_schedule={
            "summary": "Ready",
            "timeline": [],
            "total_duration_minutes": 10,
            "total_duration_minutes_max": 14,
            "error_summary": None,
            "one_oven_conflict": {
                "classification": "resequence_required",
                "temperature_gap_f": 75,
                "affected_step_ids": ["a_bake", "b_bake"],
                "remediation": {
                    "requires_resequencing": True,
                    "suggested_actions": ["Bake Recipe B after Recipe A finishes."],
                    "delaying_recipe_names": ["Recipe B"],
                    "blocking_recipe_names": ["Recipe A"],
                    "notes": "Single-oven schedule remains feasible with staged oven windows.",
                },
            },
        },
        result_recipes=[],
    )
    mock_db.seed(Session, session.session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/sessions/{session.session_id}/results")

    assert resp.status_code == 200
    data = resp.json()
    assert data["schedule"]["total_duration_minutes_max"] == 14
    assert data["schedule"]["one_oven_conflict"] == {
        "classification": "resequence_required",
        "tolerance_f": 15,
        "has_second_oven": False,
        "temperature_gap_f": 75,
        "blocking_recipe_names": [],
        "affected_step_ids": ["a_bake", "b_bake"],
        "remediation": {
            "requires_resequencing": True,
            "suggested_actions": ["Bake Recipe B after Recipe A finishes."],
            "delaying_recipe_names": ["Recipe B"],
            "blocking_recipe_names": ["Recipe A"],
            "notes": "Single-oven schedule remains feasible with staged oven windows.",
        },
    }


async def test_get_session_results_fallback_preserves_explicit_schedule_metadata(app_with_overrides, mock_db, test_user):
    session = Session(
        user_id=test_user.user_id,
        status=SessionStatus.COMPLETE,
        concept_json={},
        result_schedule=None,
        result_recipes=None,
    )
    mock_db.seed(Session, session.session_id, session)

    mock_snapshot = MagicMock()
    mock_snapshot.values = {
        "schedule": {
            "summary": "Ready",
            "timeline": [],
            "total_duration_minutes": 10,
            "total_duration_minutes_max": 14,
            "error_summary": None,
            "one_oven_conflict": {
                "classification": "resequence_required",
                "temperature_gap_f": 75,
                "affected_step_ids": ["a_bake", "b_bake"],
                "remediation": {
                    "requires_resequencing": True,
                    "suggested_actions": ["Bake Recipe B after Recipe A finishes."],
                },
            },
        },
        "validated_recipes": [],
        "errors": [],
    }
    mock_graph = AsyncMock()
    mock_graph.aget_state.return_value = mock_snapshot

    with patch("app.main.get_graph", new=AsyncMock(return_value=mock_graph)):
        transport = ASGITransport(app=app_with_overrides)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/sessions/{session.session_id}/results")

    assert resp.status_code == 200
    data = resp.json()
    assert data["schedule"]["total_duration_minutes_max"] == 14
    assert data["schedule"]["one_oven_conflict"] == {
        "classification": "resequence_required",
        "tolerance_f": 15,
        "has_second_oven": False,
        "temperature_gap_f": 75,
        "blocking_recipe_names": [],
        "affected_step_ids": ["a_bake", "b_bake"],
        "remediation": {
            "requires_resequencing": True,
            "suggested_actions": ["Bake Recipe B after Recipe A finishes."],
            "delaying_recipe_names": [],
            "blocking_recipe_names": [],
            "notes": None,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cookbook routes
# ─────────────────────────────────────────────────────────────────────────────


async def test_create_recipe_cookbook_201(app_with_overrides, test_user):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/recipe-cookbooks",
            json={"name": "Desserts", "description": "Late-course authored recipes."},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["user_id"] == str(test_user.user_id)
    assert data["name"] == "Desserts"
    assert data["description"] == "Late-course authored recipes."


async def test_list_recipe_cookbooks_returns_only_current_user_records(app_with_overrides, mock_db, test_user):
    owned = RecipeCookbookRecord(user_id=test_user.user_id, name="Desserts", description="Sweets")
    other = RecipeCookbookRecord(user_id=uuid.uuid4(), name="Hidden", description="Should not leak")
    mock_db.seed(RecipeCookbookRecord, owned.cookbook_id, owned)
    mock_db.seed(RecipeCookbookRecord, other.cookbook_id, other)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/recipe-cookbooks")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Desserts"
    assert data[0]["user_id"] == str(test_user.user_id)


async def test_get_recipe_cookbook_requires_ownership(app_with_overrides, mock_db):
    record = RecipeCookbookRecord(user_id=uuid.uuid4(), name="Private", description="Owned by someone else.")
    mock_db.seed(RecipeCookbookRecord, record.cookbook_id, record)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/recipe-cookbooks/{record.cookbook_id}")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


# ─────────────────────────────────────────────────────────────────────────────
# Authored recipe routes
# ─────────────────────────────────────────────────────────────────────────────


async def test_create_authored_recipe_201(app_with_overrides, authored_recipe_payload):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/authored-recipes", json=authored_recipe_payload)

    assert resp.status_code == 201
    data = resp.json()
    assert data["user_id"] == authored_recipe_payload["user_id"]
    assert data["title"] == authored_recipe_payload["title"]
    assert data["yield_info"]["quantity"] == authored_recipe_payload["yield_info"]["quantity"]
    assert data["steps"][1]["dependencies"][0]["step_id"] == "roasted_carrots_with_labneh_step_1"
    assert data["cookbook_id"] is None
    assert data["cookbook"] is None
    assert "status" not in data
    assert "concept_json" not in data


async def test_create_authored_recipe_with_cookbook_metadata(app_with_overrides, mock_db, test_user, authored_recipe_payload):
    cookbook = RecipeCookbookRecord(user_id=test_user.user_id, name="Desserts", description="Sweet course ideas")
    mock_db.seed(RecipeCookbookRecord, cookbook.cookbook_id, cookbook)
    payload = dict(authored_recipe_payload)
    payload["cookbook_id"] = str(cookbook.cookbook_id)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/authored-recipes", json=payload)

    assert resp.status_code == 201
    data = resp.json()
    assert data["cookbook_id"] == str(cookbook.cookbook_id)
    assert data["cookbook"]["name"] == "Desserts"


async def test_create_authored_recipe_rejects_missing_cookbook(app_with_overrides, authored_recipe_payload):
    payload = dict(authored_recipe_payload)
    payload["cookbook_id"] = str(uuid.uuid4())

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/authored-recipes", json=payload)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Cookbook not found"


async def test_create_authored_recipe_rejects_other_users_cookbook(app_with_overrides, mock_db, authored_recipe_payload):
    cookbook = RecipeCookbookRecord(user_id=uuid.uuid4(), name="Hidden", description="Not yours")
    mock_db.seed(RecipeCookbookRecord, cookbook.cookbook_id, cookbook)
    payload = dict(authored_recipe_payload)
    payload["cookbook_id"] = str(cookbook.cookbook_id)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/authored-recipes", json=payload)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


async def test_list_authored_recipes_returns_only_current_user_records(app_with_overrides, mock_db, test_user):
    cookbook = RecipeCookbookRecord(user_id=test_user.user_id, name="Vegetables", description="Produce-forward")
    mock_db.seed(RecipeCookbookRecord, cookbook.cookbook_id, cookbook)

    owned = AuthoredRecipeRecord(
        user_id=test_user.user_id,
        cookbook_id=cookbook.cookbook_id,
        title="Warm Chicories",
        description="Bitter greens course.",
        cuisine="French",
        authored_payload={
            "title": "Warm Chicories",
            "description": "Bitter greens course.",
            "cuisine": "French",
            "yield_info": {"quantity": 2, "unit": "plates"},
            "ingredients": [{"name": "chicories", "quantity": "2 heads"}],
            "steps": [
                {
                    "title": "Dress",
                    "instruction": "Dress and serve.",
                    "duration_minutes": 5,
                    "resource": "hands",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    other = AuthoredRecipeRecord(
        user_id=uuid.uuid4(),
        title="Hidden Recipe",
        description="Should not leak.",
        cuisine="Secret",
        authored_payload={
            "title": "Hidden Recipe",
            "description": "Should not leak.",
            "cuisine": "Secret",
            "yield_info": {"quantity": 1, "unit": "plate"},
            "ingredients": [{"name": "something", "quantity": "1"}],
            "steps": [
                {
                    "title": "Hide",
                    "instruction": "Keep hidden.",
                    "duration_minutes": 1,
                    "resource": "hands",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    mock_db.seed(AuthoredRecipeRecord, owned.recipe_id, owned)
    mock_db.seed(AuthoredRecipeRecord, other.recipe_id, other)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/authored-recipes")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Warm Chicories"
    assert data[0]["user_id"] == str(test_user.user_id)
    assert data[0]["cookbook_id"] == str(cookbook.cookbook_id)
    assert data[0]["cookbook"]["name"] == "Vegetables"


async def test_get_authored_recipe_requires_ownership(app_with_overrides, mock_db):
    record = AuthoredRecipeRecord(
        user_id=uuid.uuid4(),
        title="Private Recipe",
        description="Owned by someone else.",
        cuisine="Private",
        authored_payload={
            "title": "Private Recipe",
            "description": "Owned by someone else.",
            "cuisine": "Private",
            "yield_info": {"quantity": 1, "unit": "plate"},
            "ingredients": [{"name": "secret", "quantity": "1"}],
            "steps": [
                {
                    "title": "Keep private",
                    "instruction": "Do not expose.",
                    "duration_minutes": 1,
                    "resource": "hands",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    mock_db.seed(AuthoredRecipeRecord, record.recipe_id, record)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/authored-recipes/{record.recipe_id}")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


async def test_get_authored_recipe_returns_full_contract_shape(app_with_overrides, mock_db, test_user, authored_recipe_payload):
    cookbook = RecipeCookbookRecord(user_id=test_user.user_id, name="Desserts", description="Sweet endings")
    mock_db.seed(RecipeCookbookRecord, cookbook.cookbook_id, cookbook)

    payload = dict(authored_recipe_payload)
    payload.pop("user_id")
    record = AuthoredRecipeRecord(
        user_id=test_user.user_id,
        cookbook_id=cookbook.cookbook_id,
        title=payload["title"],
        description=payload["description"],
        cuisine=payload["cuisine"],
        authored_payload=payload,
    )
    mock_db.seed(AuthoredRecipeRecord, record.recipe_id, record)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/authored-recipes/{record.recipe_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["recipe_id"] == str(record.recipe_id)
    assert data["user_id"] == str(test_user.user_id)
    assert data["storage"]["method"] == "refrigerated"
    assert data["steps"][0]["resource"] == "oven"
    assert data["cookbook_id"] == str(cookbook.cookbook_id)
    assert data["cookbook"]["name"] == "Desserts"
    assert "status" not in data


async def test_update_authored_recipe_cookbook_allows_assign_and_unassign(app_with_overrides, mock_db, test_user):
    cookbook = RecipeCookbookRecord(user_id=test_user.user_id, name="Mexican", description="Regional drafts")
    mock_db.seed(RecipeCookbookRecord, cookbook.cookbook_id, cookbook)
    record = AuthoredRecipeRecord(
        user_id=test_user.user_id,
        title="Mole Negro",
        description="Sauce draft.",
        cuisine="Mexican",
        authored_payload={
            "title": "Mole Negro",
            "description": "Sauce draft.",
            "cuisine": "Mexican",
            "yield_info": {"quantity": 4, "unit": "portions"},
            "ingredients": [{"name": "chilies", "quantity": "12"}],
            "steps": [
                {
                    "title": "Toast",
                    "instruction": "Toast aromatics.",
                    "duration_minutes": 10,
                    "resource": "stovetop",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    mock_db.seed(AuthoredRecipeRecord, record.recipe_id, record)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        assign_resp = await ac.patch(
            f"/api/v1/authored-recipes/{record.recipe_id}/cookbook",
            json={"cookbook_id": str(cookbook.cookbook_id)},
        )
        unassign_resp = await ac.patch(
            f"/api/v1/authored-recipes/{record.recipe_id}/cookbook",
            json={"cookbook_id": None},
        )

    assert assign_resp.status_code == 200
    assign_data = assign_resp.json()
    assert assign_data["cookbook_id"] == str(cookbook.cookbook_id)
    assert assign_data["cookbook"]["name"] == "Mexican"

    assert unassign_resp.status_code == 200
    unassign_data = unassign_resp.json()
    assert unassign_data["cookbook_id"] is None
    assert unassign_data["cookbook"] is None


async def test_update_authored_recipe_cookbook_rejects_missing_cookbook(app_with_overrides, mock_db, test_user):
    record = AuthoredRecipeRecord(
        user_id=test_user.user_id,
        title="Mole Negro",
        description="Sauce draft.",
        cuisine="Mexican",
        authored_payload={
            "title": "Mole Negro",
            "description": "Sauce draft.",
            "cuisine": "Mexican",
            "yield_info": {"quantity": 4, "unit": "portions"},
            "ingredients": [{"name": "chilies", "quantity": "12"}],
            "steps": [
                {
                    "title": "Toast",
                    "instruction": "Toast aromatics.",
                    "duration_minutes": 10,
                    "resource": "stovetop",
                    "required_equipment": [],
                    "dependencies": [],
                    "can_be_done_ahead": False,
                    "prep_ahead_window": None,
                    "prep_ahead_notes": None,
                    "target_internal_temperature_f": None,
                    "until_condition": None,
                    "yield_contribution": None,
                    "chef_notes": None,
                }
            ],
            "equipment_notes": [],
            "storage": None,
            "hold": None,
            "reheat": None,
            "make_ahead_guidance": None,
            "plating_notes": None,
            "chef_notes": None,
        },
    )
    mock_db.seed(AuthoredRecipeRecord, record.recipe_id, record)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.patch(
            f"/api/v1/authored-recipes/{record.recipe_id}/cookbook",
            json={"cookbook_id": str(uuid.uuid4())},
        )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Cookbook not found"


async def test_create_authored_recipe_rejects_cross_user_body(app_with_overrides, authored_recipe_payload):
    payload = dict(authored_recipe_payload)
    payload["user_id"] = str(uuid.uuid4())

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/authored-recipes", json=payload)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


async def test_create_authored_recipe_422_preserves_validation_detail(app_with_overrides, authored_recipe_payload):
    payload = dict(authored_recipe_payload)
    payload["steps"] = [
        {
            **payload["steps"][0],
            "can_be_done_ahead": True,
            "prep_ahead_window": None,
        }
    ]

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/authored-recipes", json=payload)

    assert resp.status_code == 422
    data = resp.json()
    assert isinstance(data["detail"], list)
    assert len(data["detail"]) == 1
    issue = data["detail"][0]
    assert issue["type"] == "value_error"
    assert issue["loc"][:3] == ["body", "steps", 0]
    assert "prep_ahead_window is required" in issue["msg"]
    assert "Access denied" not in issue["msg"]


# ─────────────────────────────────────────────────────────────────────────────
# Ingest routes (Fix #7)
# ─────────────────────────────────────────────────────────────────────────────


async def test_upload_pdf_rejects_non_pdf(app_with_overrides):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/ingest",
            files={"file": ("notes.txt", b"not a pdf", "text/plain")},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Only PDF files accepted"


async def test_get_ingestion_status_requires_ownership(app_with_overrides, mock_db):
    job = IngestionJob(user_id=uuid.uuid4(), status=IngestionStatus.PENDING)
    mock_db.seed(IngestionJob, job.job_id, job)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/ingest/{job.job_id}")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"
