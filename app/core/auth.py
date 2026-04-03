"""
core/auth.py
Authentication dependency for FastAPI routes.

Supports JWT Bearer token authentication:
  Authorization: Bearer <jwt>

The dependency injection pattern means NO route changes are needed.
Every route uses CurrentUser from core/deps.py, which calls get_current_user().
"""

import uuid
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.settings import get_settings
from app.db.session import get_session
from app.models.user import UserProfile

_AUTH_ERROR = "Missing or invalid authentication token."


def _decode_jwt(token: str) -> dict:
    """Decode and validate an access JWT token. Raises HTTPException on failure."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail=_AUTH_ERROR)
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail=_AUTH_ERROR)

    # Reject refresh tokens used as access tokens
    if payload.get("type") == "refresh":
        raise HTTPException(status_code=401, detail=_AUTH_ERROR)

    return payload


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_session),
) -> UserProfile:
    """
    Auth dependency. Requires a valid JWT Bearer token.
    Returns the authenticated UserProfile.
    """
    user_id: Optional[uuid.UUID] = None

    # Require JWT Bearer token
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        payload = _decode_jwt(token)
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail=_AUTH_ERROR)
        try:
            user_id = uuid.UUID(sub)
        except ValueError:
            raise HTTPException(status_code=401, detail=_AUTH_ERROR)

    else:
        raise HTTPException(status_code=401, detail=_AUTH_ERROR)

    result = await db.exec(select(UserProfile).where(UserProfile.user_id == user_id))
    user = result.first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


def is_admin_user(user: UserProfile) -> bool:
    """Return whether the authenticated user matches the configured admin identity."""
    settings = get_settings()
    admin_email = settings.admin_email.strip().lower()
    return bool(admin_email) and user.email.strip().lower() == admin_email


def ensure_admin_user(user: UserProfile) -> UserProfile:
    """Require the authenticated caller to match the configured admin email."""
    if not is_admin_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
