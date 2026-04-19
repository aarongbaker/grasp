"""Tests for invite-gated registration and admin-issued invite consumption."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi.errors import RateLimitExceeded
from sqlmodel import select

from app.api.routes.admin import router as admin_router
from app.api.routes.users import router as users_router
from app.core.settings import Settings
from app.db.session import get_session
from app.models.invite import Invite
from app.models.user import UserProfile


@pytest.fixture
def invite_contract_settings(admin_route_settings):
    return admin_route_settings.model_copy(update={"invite_codes_enabled": True})


def _create_invite_app(test_db_session, include_admin: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(users_router, prefix="/api/v1")
    if include_admin:
        app.include_router(admin_router, prefix="/api/v1")
    app.state.limiter = MagicMock()

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(status_code=429, content={"detail": f"Rate limit exceeded: {exc.detail}"})

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_session] = override_db
    return app


@pytest.fixture
def invite_app(test_db_session):
    return _create_invite_app(test_db_session)


@pytest_asyncio.fixture
async def valid_invite(test_db_session):
    invite = Invite(code="VALID-CODE-123", email="invited@example.com")
    test_db_session.add(invite)
    await test_db_session.commit()
    await test_db_session.refresh(invite)
    return invite


@pytest_asyncio.fixture
async def claimed_invite(test_db_session):
    invite = Invite(
        code="CLAIMED-CODE-123",
        email="claimed@example.com",
        claimed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    test_db_session.add(invite)
    await test_db_session.commit()
    await test_db_session.refresh(invite)
    return invite


@pytest_asyncio.fixture
async def expired_invite(test_db_session):
    invite = Invite(
        code="EXPIRED-CODE-123",
        email="expired@example.com",
        expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None),
    )
    test_db_session.add(invite)
    await test_db_session.commit()
    await test_db_session.refresh(invite)
    return invite


@pytest_asyncio.fixture
async def client(invite_app, invite_contract_settings):
    transport = ASGITransport(app=invite_app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        with patch("app.api.routes.users.get_settings", return_value=invite_contract_settings), patch(
            "app.core.auth.get_settings", return_value=invite_contract_settings
        ):
            yield async_client
    invite_app.dependency_overrides.clear()
@pytest.mark.asyncio
async def test_valid_invite_flow(client, test_db_session, valid_invite):
    response = await client.post(
        "/api/v1/users",
        json={
            "name": "Invited User",
            "email": valid_invite.email,
            "password": "strongpassword123",
            "invite_code": valid_invite.code,
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["email"] == valid_invite.email

    result = await test_db_session.exec(select(UserProfile).where(UserProfile.email == valid_invite.email))
    assert result.first() is not None

    result = await test_db_session.exec(select(Invite).where(Invite.code == valid_invite.code))
    invite = result.first()
    assert invite.claimed_at is not None


@pytest.mark.asyncio
async def test_admin_issued_invite_can_be_consumed_by_registration(
    client, test_db_session, admin_user, invite_contract_settings, access_token_for
):
    issue_response = await client.post(
        "/api/v1/admin/invites",
        headers={"Authorization": f"Bearer {access_token_for(admin_user, invite_contract_settings)}"},
        json={"email": "issued@example.com"},
    )

    assert issue_response.status_code == 201, issue_response.text
    invite_payload = issue_response.json()
    invite_code = invite_payload["code"]
    assert invite_payload["email"] == "issued@example.com"
    assert invite_payload["expires_at"]

    register_response = await client.post(
        "/api/v1/users",
        json={
            "name": "Issued User",
            "email": "issued@example.com",
            "password": "strongpassword123",
            "invite_code": invite_code,
        },
    )

    assert register_response.status_code == 201, register_response.text
    data = register_response.json()
    assert data["email"] == "issued@example.com"

    invite_result = await test_db_session.exec(select(Invite).where(Invite.code == invite_code))
    invite = invite_result.first()
    assert invite is not None
    assert invite.email == "issued@example.com"
    assert invite.claimed_at is not None


@pytest.mark.asyncio
async def test_missing_invite_code_when_gating_enabled(client):
    response = await client.post(
        "/api/v1/users",
        json={
            "name": "No Invite",
            "email": "noinvite@example.com",
            "password": "strongpassword123",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invite code is required"


@pytest.mark.asyncio
async def test_invalid_invite_code(client):
    response = await client.post(
        "/api/v1/users",
        json={
            "name": "Bad Invite",
            "email": "badinvite@example.com",
            "password": "strongpassword123",
            "invite_code": "NONEXISTENT-CODE",
        },
    )

    assert response.status_code == 400
    assert "Invalid invite code" in response.json()["detail"]


@pytest.mark.asyncio
async def test_already_claimed_invite(client, claimed_invite):
    response = await client.post(
        "/api/v1/users",
        json={
            "name": "Claimed Invite",
            "email": claimed_invite.email,
            "password": "strongpassword123",
            "invite_code": claimed_invite.code,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invite code has already been used"


@pytest.mark.asyncio
async def test_expired_invite(client, expired_invite):
    response = await client.post(
        "/api/v1/users",
        json={
            "name": "Expired Invite",
            "email": expired_invite.email,
            "password": "strongpassword123",
            "invite_code": expired_invite.code,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invite code has expired"


@pytest.mark.asyncio
async def test_email_mismatch(client, valid_invite):
    response = await client.post(
        "/api/v1/users",
        json={
            "name": "Mismatch",
            "email": "different@example.com",
            "password": "strongpassword123",
            "invite_code": valid_invite.code,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invite code does not match email address"


@pytest.mark.asyncio
async def test_invite_gating_disabled(test_db_session):
    app = _create_invite_app(test_db_session, include_admin=False)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.api.routes.users.get_settings", return_value=Settings(invite_codes_enabled=False)):
            response = await client.post(
                "/api/v1/users",
                json={
                    "name": "No Gate",
                    "email": "nogate@example.com",
                    "password": "strongpassword123",
                },
            )

    assert response.status_code == 201, response.text
    app.dependency_overrides.clear()
