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
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.models.authored_recipe import AuthoredRecipeRecord, RecipeCookbookRecord
from app.models.enums import ChunkType, IngestionStatus, SessionStatus
from app.models.ingestion import BookRecord, CookbookChunk, IngestionJob
from app.models.session import Session
from app.models.user import KitchenConfig, UserProfile

# ─────────────────────────────────────────────────────────────────────────────
# Test app + fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _create_test_app() -> FastAPI:
    """Create a FastAPI app with routes but no lifespan (no external deps)."""
    from app.api.routes.authored_recipes import router as authored_recipes_router
    from app.api.routes.health import router as health_router
    from app.api.routes.ingest import router as ingest_router
    from app.api.routes.recipe_cookbooks import router as recipe_cookbooks_router
    from app.api.routes.sessions import router as sessions_router
    from app.api.routes.users import router as users_router

    app = FastAPI()
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


class MockDBSession:
    """
    Minimal mock of AsyncSession for route testing.
    Stores objects in memory. Supports add, commit, refresh, get.
    """

    def __init__(self):
        self._store: dict[tuple, object] = {}
        self.exec_result = None

    def add(self, obj):
        model_class = obj.__class__
        for pk_name in ("session_id", "job_id", "recipe_id", "cookbook_id", "user_id"):
            if hasattr(obj, pk_name):
                pk = getattr(obj, pk_name)
                if pk is not None:
                    self._store[(model_class, pk)] = obj
                break

    async def commit(self):
        pass

    async def flush(self):
        pass

    async def refresh(self, obj):
        # Simulate assigning a UUID if not already set
        if hasattr(obj, "session_id") and obj.session_id is None:
            obj.session_id = uuid.uuid4()
        if hasattr(obj, "job_id") and obj.job_id is None:
            obj.job_id = uuid.uuid4()
        if hasattr(obj, "recipe_id") and obj.recipe_id is None:
            obj.recipe_id = uuid.uuid4()
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

    async def exec(self, stmt):
        """Stub for select queries."""
        if self.exec_result is not None:
            return self.exec_result

        statement_text = str(stmt)
        if "FROM authored_recipes" in statement_text:
            user_id = None
            for criterion in getattr(stmt, "_where_criteria", ()):
                right = getattr(criterion, "right", None)
                value = getattr(right, "value", None)
                if isinstance(value, uuid.UUID):
                    user_id = value
            rows = [
                obj
                for (model_class, _), obj in self._store.items()
                if model_class is AuthoredRecipeRecord and (user_id is None or obj.user_id == user_id)
            ]
            rows.sort(key=lambda record: record.updated_at, reverse=True)
            mock_result = MagicMock()
            mock_result.first.return_value = rows[0] if rows else None
            mock_result.all.return_value = rows
            return mock_result

        if "FROM recipe_cookbooks" in statement_text:
            user_id = None
            for criterion in getattr(stmt, "_where_criteria", ()):
                right = getattr(criterion, "right", None)
                value = getattr(right, "value", None)
                if isinstance(value, uuid.UUID):
                    user_id = value
            rows = [
                obj
                for (model_class, _), obj in self._store.items()
                if model_class is RecipeCookbookRecord and (user_id is None or obj.user_id == user_id)
            ]
            rows.sort(key=lambda record: (record.updated_at, record.name), reverse=True)
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


# ─────────────────────────────────────────────────────────────────────────────
# Auth edge cases
# ─────────────────────────────────────────────────────────────────────────────


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


async def test_create_session_201(app_with_overrides, test_user):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "free_text": "Dinner party with lamb and rosemary.",
                "guest_count": 4,
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
    assert "gluten-free" in concept["dietary_restrictions"]
    assert "nut-free" in concept["dietary_restrictions"]


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


async def test_get_session_status_requires_ownership(app_with_overrides, mock_db):
    session = Session(user_id=uuid.uuid4(), status=SessionStatus.PENDING, concept_json={})
    mock_db.seed(Session, session.session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/sessions/{session.session_id}")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


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
