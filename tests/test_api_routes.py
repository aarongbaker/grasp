"""
tests/test_api_routes.py
HTTP-level tests for FastAPI routes using httpx AsyncClient.

Tests cover:
  - Auth: invalid UUID → 400, unknown UUID → 404 (Fix #10)
  - Sessions: create, run (409 guard, 403 ownership), get status (Fix #7)
  - Ingest: PDF-only validation (400), ownership on status poll (Fix #7)

Uses dependency overrides with mock DB session to avoid needing a real
Postgres instance. Tests verify HTTP status codes and request validation.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from models.user import UserProfile, KitchenConfig
from models.session import Session
from models.ingestion import IngestionJob
from models.enums import SessionStatus, IngestionStatus


# ─────────────────────────────────────────────────────────────────────────────
# Test app + fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _create_test_app() -> FastAPI:
    """Create a FastAPI app with routes but no lifespan (no external deps)."""
    from api.routes.health import router as health_router
    from api.routes.users import router as users_router
    from api.routes.sessions import router as sessions_router
    from api.routes.ingest import router as ingest_router

    app = FastAPI()
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(sessions_router, prefix="/api/v1")
    app.include_router(ingest_router, prefix="/api/v1")
    return app


def _make_test_user() -> UserProfile:
    """Create a UserProfile instance (not persisted — for mock injection)."""
    user = UserProfile(
        user_id=uuid.uuid4(),
        name="Test Chef",
        email="chef@test.com",
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

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        # Simulate assigning a UUID if not already set
        if hasattr(obj, "session_id") and obj.session_id is None:
            obj.session_id = uuid.uuid4()
        if hasattr(obj, "job_id") and obj.job_id is None:
            obj.job_id = uuid.uuid4()

    async def get(self, model_class, pk):
        return self._store.get((model_class, pk))

    def seed(self, model_class, pk, obj):
        """Pre-populate a row for get() to find."""
        self._store[(model_class, pk)] = obj

    async def exec(self, stmt):
        """Stub for select queries."""
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
    from db.session import get_session
    from core.auth import get_current_user

    app = _create_test_app()

    async def _override_session():
        yield mock_db

    async def _override_user():
        return test_user

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = _override_user
    return app


# ─────────────────────────────────────────────────────────────────────────────
# Auth edge cases (Fix #10)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_invalid_uuid_returns_400():
    """X-User-ID with a non-UUID value should return 400."""
    from db.session import get_session

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
            headers={"X-User-ID": "not-a-uuid"},
        )
    assert resp.status_code == 400
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_auth_unknown_uuid_returns_404():
    """X-User-ID with a valid but nonexistent UUID should return 404."""
    from db.session import get_session

    app = _create_test_app()
    mock_db = MockDBSession()

    async def _override_session():
        yield mock_db

    app.dependency_overrides[get_session] = _override_session
    # Real auth runs — mock_db.exec returns None for select query
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        fake_id = str(uuid.uuid4())
        resp = await ac.get(
            "/api/v1/sessions/00000000-0000-0000-0000-000000000000",
            headers={"X-User-ID": fake_id},
        )
    assert resp.status_code == 404
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Session routes (Fix #7)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_session_201(app_with_overrides, test_user):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/sessions", json={
            "free_text": "Dinner party with lamb and rosemary.",
            "guest_count": 4,
            "meal_type": "dinner",
            "occasion": "dinner_party",
            "dietary_restrictions": ["nut-free"],
        })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    # dietary_defaults merged: gluten-free from user + nut-free from request
    concept = data["concept_json"]
    assert "gluten-free" in concept["dietary_restrictions"]
    assert "nut-free" in concept["dietary_restrictions"]


@pytest.mark.asyncio
async def test_create_session_invalid_guest_count(app_with_overrides):
    """guest_count=0 is rejected at the request body layer (422)."""
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/sessions", json={
            "free_text": "Quick dinner.",
            "guest_count": 0,
            "meal_type": "dinner",
            "occasion": "casual",
        })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_session_not_found(app_with_overrides):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        fake_id = str(uuid.uuid4())
        resp = await ac.get(f"/api/v1/sessions/{fake_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_session_pending_returns_row(app_with_overrides, mock_db, test_user):
    """PENDING sessions return the raw DB row (no checkpoint query)."""
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user.user_id,
        status=SessionStatus.PENDING,
        concept_json={"free_text": "test", "guest_count": 2,
                       "meal_type": "dinner", "occasion": "casual",
                       "dietary_restrictions": []},
    )
    mock_db.seed(Session, session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_get_session_403_wrong_user(app_with_overrides, mock_db, test_user):
    """Session owned by a different user returns 403."""
    session_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=other_user_id,  # different user
        status=SessionStatus.PENDING,
        concept_json={},
    )
    mock_db.seed(Session, session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_run_pipeline_409_on_non_pending(app_with_overrides, mock_db, test_user):
    """Attempting to run an already-GENERATING session returns 409."""
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user.user_id,
        status=SessionStatus.GENERATING,
        concept_json={},
    )
    mock_db.seed(Session, session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(f"/api/v1/sessions/{session_id}/run")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_run_pipeline_404_unknown_session(app_with_overrides):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        fake_id = str(uuid.uuid4())
        resp = await ac.post(f"/api/v1/sessions/{fake_id}/run")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_run_pipeline_202_enqueues(app_with_overrides, mock_db, test_user):
    """POST /sessions/{id}/run on PENDING session returns 202."""
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user.user_id,
        status=SessionStatus.PENDING,
        concept_json={},
    )
    mock_db.seed(Session, session_id, session)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Patch at workers.tasks — the route imports from there at call time
        with patch("workers.tasks.run_grasp_pipeline") as mock_task:
            mock_task.delay = MagicMock()
            resp = await ac.post(f"/api/v1/sessions/{session_id}/run")

    assert resp.status_code == 202
    assert resp.json()["status"] == "generating"


# ─────────────────────────────────────────────────────────────────────────────
# Ingest routes (Fix #7)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_non_pdf_returns_400(app_with_overrides):
    """Only .pdf files accepted."""
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/ingest",
            files={"file": ("notes.txt", b"hello world", "text/plain")},
        )
    assert resp.status_code == 400
    assert "PDF" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_pdf_returns_202(app_with_overrides):
    """Valid PDF upload returns 202 with job_id."""
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Patch at workers.tasks — the route imports from there at call time
        with patch("workers.tasks.ingest_cookbook") as mock_task:
            mock_task.delay = MagicMock()
            resp = await ac.post(
                "/api/v1/ingest",
                files={"file": ("cookbook.pdf", b"%PDF-1.4 fake content", "application/pdf")},
            )
    assert resp.status_code == 202
    assert "job_id" in resp.json()


@pytest.mark.asyncio
async def test_get_ingestion_status_404(app_with_overrides):
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        fake_id = str(uuid.uuid4())
        resp = await ac.get(f"/api/v1/ingest/{fake_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_ingestion_status_403_wrong_user(app_with_overrides, mock_db, test_user):
    """Ingestion job owned by a different user returns 403."""
    job_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    job = IngestionJob(
        job_id=job_id,
        user_id=other_user_id,  # different user
        status=IngestionStatus.PENDING,
    )
    mock_db.seed(IngestionJob, job_id, job)

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/ingest/{job_id}")
    assert resp.status_code == 403
