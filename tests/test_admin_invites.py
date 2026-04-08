"""Tests for admin invite issuance contract."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi.errors import RateLimitExceeded
from sqlmodel import select

from app.api.routes.admin import router as admin_router
from app.db.session import get_session
from app.models.invite import Invite


@pytest.fixture
def admin_invite_app(test_db_session):
    """FastAPI app with the real admin router and test DB session."""
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/v1")
    app.state.limiter = MagicMock()

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(status_code=429, content={"detail": f"Rate limit exceeded: {exc.detail}"})

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_session] = override_db
    return app


@pytest.mark.asyncio
async def test_admin_can_issue_invite_and_persist_email(
    admin_invite_app, admin_user, admin_route_settings, access_token_for, test_db_session
):
    """Configured admin can create an invite that is stored against the requested email."""
    transport = ASGITransport(app=admin_invite_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.core.auth.get_settings", lambda: admin_route_settings)
            response = await ac.post(
                "/api/v1/admin/invites",
                headers={"Authorization": f"Bearer {access_token_for(admin_user, admin_route_settings)}"},
                json={"email": "guest@example.com"},
            )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["email"] == "guest@example.com"
    assert payload["code"]
    assert payload["claimed_at"] is None
    assert payload["created_at"]
    assert payload["expires_at"]

    result = await test_db_session.exec(select(Invite).where(Invite.code == payload["code"]))
    invite = result.first()
    assert invite is not None
    assert invite.email == "guest@example.com"
    assert invite.claimed_at is None
    assert invite.expires_at > invite.created_at


@pytest.mark.asyncio
async def test_non_admin_cannot_issue_invite(
    admin_invite_app, non_admin_user, admin_route_settings, access_token_for, test_db_session
):
    """Authenticated non-admin callers get the stable denial response and no row is created."""
    transport = ASGITransport(app=admin_invite_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.core.auth.get_settings", lambda: admin_route_settings)
            response = await ac.post(
                "/api/v1/admin/invites",
                headers={"Authorization": f"Bearer {access_token_for(non_admin_user, admin_route_settings)}"},
                json={"email": "blocked@example.com"},
            )

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"

    result = await test_db_session.exec(select(Invite).where(Invite.email == "blocked@example.com"))
    assert result.first() is None


@pytest.mark.asyncio
async def test_missing_auth_is_rejected_before_invite_creation(admin_invite_app, test_db_session):
    """Unauthenticated callers are rejected by the shared auth dependency."""
    blocked_email = f"guest-{uuid.uuid4()}@example.com"

    transport = ASGITransport(app=admin_invite_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/admin/invites",
            json={"email": blocked_email},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid authentication token."

    result = await test_db_session.exec(select(Invite).where(Invite.email == blocked_email))
    assert result.first() is None
