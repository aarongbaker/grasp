"""
tests/test_auth.py
Tests for JWT bearer token auth (JWT-only).
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest

from app.core.settings import get_settings

settings = get_settings()


def _make_token(user_id: str, expired: bool = False) -> str:
    """Create a JWT token for testing."""
    now = datetime.now(timezone.utc)
    exp = now - timedelta(hours=1) if expired else now + timedelta(hours=1)
    payload = {"sub": user_id, "iat": now, "exp": exp}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _mock_user(user_id: uuid.UUID):
    """Create a mock UserProfile."""
    user = MagicMock()
    user.user_id = user_id
    user.email = "test@test.com"
    user.dietary_defaults = []
    return user


@pytest.mark.asyncio
async def test_jwt_bearer_auth_valid():
    """Valid JWT token should authenticate successfully."""
    from app.core.auth import get_current_user

    user_id = uuid.uuid4()
    token = _make_token(str(user_id))
    mock_user = _mock_user(user_id)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.first.return_value = mock_user
    mock_db.exec.return_value = mock_result

    result = await get_current_user(
        authorization=f"Bearer {token}",
        db=mock_db,
    )
    assert result.user_id == user_id


@pytest.mark.asyncio
async def test_jwt_bearer_auth_expired():
    """Expired JWT token should return 401 with generic error message."""
    from fastapi import HTTPException

    from app.core.auth import get_current_user

    token = _make_token(str(uuid.uuid4()), expired=True)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            authorization=f"Bearer {token}",
            db=AsyncMock(),
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing or invalid authentication token."


@pytest.mark.asyncio
async def test_jwt_bearer_auth_malformed():
    """Malformed JWT token should return 401."""
    from fastapi import HTTPException

    from app.core.auth import get_current_user

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            authorization="Bearer not-a-valid-token",
            db=AsyncMock(),
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_no_auth_headers_returns_401():
    """Missing auth header should return 401 with generic error message."""
    from fastapi import HTTPException

    from app.core.auth import get_current_user

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            authorization=None,
            db=AsyncMock(),
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing or invalid authentication token."


@pytest.mark.asyncio
async def test_jwt_user_not_found_returns_404():
    """Valid JWT but user doesn't exist should return 404."""
    from fastapi import HTTPException

    from app.core.auth import get_current_user

    token = _make_token(str(uuid.uuid4()))

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.first.return_value = None
    mock_db.exec.return_value = mock_result

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            authorization=f"Bearer {token}",
            db=mock_db,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_jwt_bearer_auth_generic_error_message():
    """All auth failures return the same generic message to prevent info leakage."""
    from fastapi import HTTPException
    from app.core.auth import get_current_user

    generic_msg = "Missing or invalid authentication token."

    # Expired token
    token = _make_token(str(uuid.uuid4()), expired=True)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization=f"Bearer {token}", db=AsyncMock())
    assert exc_info.value.detail == generic_msg

    # Malformed token
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization="Bearer garbage", db=AsyncMock())
    assert exc_info.value.detail == generic_msg

    # No auth at all
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization=None, db=AsyncMock())
    assert exc_info.value.detail == generic_msg
