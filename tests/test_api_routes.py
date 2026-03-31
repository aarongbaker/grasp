"""
tests/test_api_routes.py
HTTP-level tests for FastAPI routes using httpx AsyncClient.

Tests cover:
  - Auth: malformed JWT → 401, valid JWT unknown user → 404
  - Sessions: create, run (409 guard, 403 ownership), get status (Fix #7)
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

from app.models.enums import ChunkType, IngestionStatus, SessionStatus
from app.models.ingestion import BookRecord, CookbookChunk, IngestionJob
from app.models.session import Session
from app.models.user import KitchenConfig, UserProfile

# ─────────────────────────────────────────────────────────────────────────────
# Test app + fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _create_test_app() -> FastAPI:
    """Create a FastAPI app with routes but no lifespan (no external deps)."""
    from app.api.routes.health import router as health_router
    from app.api.routes.ingest import router as ingest_router
    from app.api.routes.sessions import router as sessions_router
    from app.api.routes.users import router as users_router

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
        if self.exec_result is not None:
            return self.exec_result
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


# ─────────────────────────────────────────────────────────────────────────────
# Auth edge cases
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_create_cookbook_session_201_hydrates_selected_recipes(app_with_overrides, mock_db, test_user):
    book_id = uuid.uuid4()
    first_chunk_id = uuid.uuid4()
    second_chunk_id = uuid.uuid4()
    book = BookRecord(
        book_id=book_id,
        user_id=test_user.user_id,
        title="Sunday Suppers",
        author="Test Author",
        total_pages=240,
        total_chunks=12,
    )
    first_chunk = CookbookChunk(
        chunk_id=first_chunk_id,
        book_id=book_id,
        user_id=test_user.user_id,
        text="Roast Chicken with Herbs\n1. Season the bird.\n2. Roast until done.\n3. Rest and carve.",
        chunk_type=ChunkType.RECIPE,
        chapter="Mains",
        page_number=42,
    )
    second_chunk = CookbookChunk(
        chunk_id=second_chunk_id,
        book_id=book_id,
        user_id=test_user.user_id,
        text="Burnt Honey Tart\n1. Blind bake shell.\n2. Cook filling.\n3. Bake and cool.",
        chunk_type=ChunkType.RECIPE,
        chapter="Desserts",
        page_number=118,
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [(first_chunk, book), (second_chunk, book)]
    mock_db.exec_result = mock_result

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "concept_source": "cookbook",
                "free_text": "Cookbook-selected recipes: Roast Chicken with Herbs, Burnt Honey Tart",
                "selected_recipes": [
                    {"chunk_id": str(first_chunk_id)},
                    {"chunk_id": str(second_chunk_id)},
                ],
                "guest_count": 4,
                "meal_type": "dinner",
                "occasion": "dinner_party",
                "dietary_restrictions": [],
                "serving_time": "19:00",
            },
        )

    assert resp.status_code == 201
    concept = resp.json()["concept_json"]
    assert concept["concept_source"] == "cookbook"
    assert [recipe["chunk_id"] for recipe in concept["selected_recipes"]] == [str(first_chunk_id), str(second_chunk_id)]
    assert [recipe["book_title"] for recipe in concept["selected_recipes"]] == ["Sunday Suppers", "Sunday Suppers"]
    assert concept["selected_recipes"][0]["chapter"] == "Mains"
    assert concept["selected_recipes"][1]["page_number"] == 118


@pytest.mark.asyncio
async def test_create_session_invalid_guest_count(app_with_overrides):
    """guest_count=0 is rejected at the request body layer (422)."""
    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sessions",
            json={
                "free_text": "Quick dinner.",
                "guest_count": 0,
                "meal_type": "dinner",
                "occasion": "casual",
            },
        )
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
        concept_json={
            "free_text": "test",
            "guest_count": 2,
            "meal_type": "dinner",
            "occasion": "casual",
            "dietary_restrictions": [],
        },
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
        with patch("app.workers.tasks.run_grasp_pipeline") as mock_task:
            mock_task.delay = MagicMock()
            resp = await ac.post(f"/api/v1/sessions/{session_id}/run")

    assert resp.status_code == 202
    assert resp.json()["status"] == "generating"


@pytest.mark.asyncio
async def test_run_pipeline_keeps_generating_as_only_direct_in_progress_status_write(
    app_with_overrides, mock_db, test_user
):
    """The enqueue route should only persist GENERATING and never skip ahead."""
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
        with patch("app.workers.tasks.run_grasp_pipeline") as mock_task:
            mock_task.delay = MagicMock(return_value=MagicMock(id="celery-task-123"))
            resp = await ac.post(f"/api/v1/sessions/{session_id}/run")

    assert resp.status_code == 202
    assert resp.json() == {
        "session_id": str(session_id),
        "status": "generating",
        "message": "Pipeline enqueued",
    }
    assert session.status == SessionStatus.GENERATING
    assert session.started_at is not None
    assert session.completed_at is None
    assert session.status not in {
        SessionStatus.ENRICHING,
        SessionStatus.VALIDATING,
        SessionStatus.SCHEDULING,
    }


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
        with patch("app.workers.tasks.ingest_cookbook") as mock_task:
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


@pytest.mark.asyncio
async def test_list_detected_recipes_returns_recipe_chunks_with_book_metadata(app_with_overrides, mock_db, test_user):
    book_id = uuid.uuid4()
    recipe_chunk_id = uuid.uuid4()
    book = BookRecord(
        book_id=book_id,
        user_id=test_user.user_id,
        title="Sunday Suppers",
        author="Test Author",
        total_pages=240,
        total_chunks=12,
    )
    recipe_chunk = CookbookChunk(
        chunk_id=recipe_chunk_id,
        book_id=book_id,
        user_id=test_user.user_id,
        text="Roast Chicken with Herbs\nPat the bird dry and season generously.",
        chunk_type=ChunkType.RECIPE,
        chapter="Mains",
        page_number=42,
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [(recipe_chunk, book)]
    mock_db.exec_result = mock_result

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/ingest/detected-recipes")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "chunk_id": str(recipe_chunk_id),
            "book_id": str(book_id),
            "book_title": "Sunday Suppers",
            "recipe_name": "Roast Chicken with Herbs",
            "chapter": "Mains",
            "page_number": 42,
            "text": "Roast Chicken with Herbs\nPat the bird dry and season generously.",
        }
    ]


@pytest.mark.asyncio
async def test_list_detected_recipes_recovers_title_from_ocr_heavy_chunk_before_ingredient_lines(
    app_with_overrides, mock_db, test_user
):
    book_id = uuid.uuid4()
    recipe_chunk_id = uuid.uuid4()
    book = BookRecord(
        book_id=book_id,
        user_id=test_user.user_id,
        title="Southern Suppers",
        author="Test Author",
        total_pages=320,
        total_chunks=44,
    )
    recipe_chunk = CookbookChunk(
        chunk_id=recipe_chunk_id,
        book_id=book_id,
        user_id=test_user.user_id,
        text="\nINGREDIENTS\nChicken Gumbo\n1 chicken, cut up\n2 onions, sliced\nSimmer until tender.",
        chunk_type=ChunkType.RECIPE,
        chapter="Suppers",
        page_number=114,
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [(recipe_chunk, book)]
    mock_db.exec_result = mock_result

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/ingest/detected-recipes")

    assert resp.status_code == 200
    assert resp.json()[0]["recipe_name"] == "Chicken Gumbo"


@pytest.mark.asyncio
async def test_list_detected_recipes_falls_back_when_chunk_text_has_no_nonempty_title(
    app_with_overrides, mock_db, test_user
):
    book_id = uuid.uuid4()
    recipe_chunk_id = uuid.uuid4()
    book = BookRecord(
        book_id=book_id,
        user_id=test_user.user_id,
        title="Night Kitchen",
        author="Test Author",
        total_pages=120,
        total_chunks=8,
    )
    recipe_chunk = CookbookChunk(
        chunk_id=recipe_chunk_id,
        book_id=book_id,
        user_id=test_user.user_id,
        text="\n\n  ",
        chunk_type=ChunkType.RECIPE,
        chapter="Desserts",
        page_number=77,
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [(recipe_chunk, book)]
    mock_db.exec_result = mock_result

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/ingest/detected-recipes")

    assert resp.status_code == 200
    assert resp.json()[0]["recipe_name"] == "Recipe on page 77"


@pytest.mark.asyncio
async def test_list_detected_recipes_recovers_title_from_ocr_run_on_line_when_ingredients_follow_inline(
    app_with_overrides, mock_db, test_user
):
    book_id = uuid.uuid4()
    recipe_chunk_id = uuid.uuid4()
    book = BookRecord(
        book_id=book_id,
        user_id=test_user.user_id,
        title="Southern Cook Book",
        author="Test Author",
        total_pages=320,
        total_chunks=44,
    )
    recipe_chunk = CookbookChunk(
        chunk_id=recipe_chunk_id,
        book_id=book_id,
        user_id=test_user.user_id,
        text=(
            "Creole Stuffed Peppers 4 ears of corn 6 green peppers 4 tomatoes 1 small onion "
            "1 tablespoon butter 6 green olives salt and pepper to taste Cut off tops and remove centers from peppers."
        ),
        chunk_type=ChunkType.RECIPE,
        chapter="Suppers",
        page_number=29,
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [(recipe_chunk, book)]
    mock_db.exec_result = mock_result

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/ingest/detected-recipes")

    assert resp.status_code == 200
    assert resp.json()[0]["recipe_name"] == "Creole Stuffed Peppers"


@pytest.mark.asyncio
async def test_list_detected_recipes_recovers_short_single_word_title_from_ocr_run_on_line(
    app_with_overrides, mock_db, test_user
):
    book_id = uuid.uuid4()
    recipe_chunk_id = uuid.uuid4()
    book = BookRecord(
        book_id=book_id,
        user_id=test_user.user_id,
        title="Southern Sweets",
        author="Test Author",
        total_pages=320,
        total_chunks=44,
    )
    recipe_chunk = CookbookChunk(
        chunk_id=recipe_chunk_id,
        book_id=book_id,
        user_id=test_user.user_id,
        text=(
            "Pralines 2 cups sugar 2 cups freshly-grated cocoanut 1/2 cup water Cook the sugar and water together "
            "until it makes a syrup."
        ),
        chunk_type=ChunkType.RECIPE,
        chapter="Sweets",
        page_number=48,
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [(recipe_chunk, book)]
    mock_db.exec_result = mock_result

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/ingest/detected-recipes")

    assert resp.status_code == 200
    assert resp.json()[0]["recipe_name"] == "Pralines"


@pytest.mark.asyncio
async def test_list_detected_recipes_falls_back_when_run_on_line_starts_with_ingredient_quantity(
    app_with_overrides, mock_db, test_user
):
    book_id = uuid.uuid4()
    recipe_chunk_id = uuid.uuid4()
    book = BookRecord(
        book_id=book_id,
        user_id=test_user.user_id,
        title="Southern Soups",
        author="Test Author",
        total_pages=320,
        total_chunks=44,
    )
    recipe_chunk = CookbookChunk(
        chunk_id=recipe_chunk_id,
        book_id=book_id,
        user_id=test_user.user_id,
        text=(
            "1 tablespoon butter, melted 1 tablespoon chopped green pepper 1 tablespoon chopped red pepper "
            "1 tablespoon flour 1 1/2 cups soup stock"
        ),
        chunk_type=ChunkType.RECIPE,
        chapter="Soups",
        page_number=11,
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [(recipe_chunk, book)]
    mock_db.exec_result = mock_result

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/ingest/detected-recipes")

    assert resp.status_code == 200
    assert resp.json()[0]["recipe_name"] == "Recipe on page 11"


@pytest.mark.asyncio
async def test_list_detected_recipes_falls_back_when_first_nonempty_line_is_sentence_noise(
    app_with_overrides, mock_db, test_user
):
    book_id = uuid.uuid4()
    recipe_chunk_id = uuid.uuid4()
    book = BookRecord(
        book_id=book_id,
        user_id=test_user.user_id,
        title="Field Notes",
        author="Test Author",
        total_pages=120,
        total_chunks=8,
    )
    recipe_chunk = CookbookChunk(
        chunk_id=recipe_chunk_id,
        book_id=book_id,
        user_id=test_user.user_id,
        text="Stir well and bake for 45 minutes until brown.\nSERVES SIX\n1 cup stock",
        chunk_type=ChunkType.RECIPE,
        chapter="Desserts",
        page_number=91,
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [(recipe_chunk, book)]
    mock_db.exec_result = mock_result

    transport = ASGITransport(app=app_with_overrides)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/ingest/detected-recipes")

    assert resp.status_code == 200
    assert resp.json()[0]["recipe_name"] == "Recipe on page 91"
