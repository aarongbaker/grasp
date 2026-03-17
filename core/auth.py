"""
core/auth.py
Authentication dependency for FastAPI routes.

Supports two auth methods (checked in order):
  1. Authorization: Bearer <jwt> — production method
  2. X-User-ID: <uuid> — legacy dev method (deprecated, will be removed)

The dependency injection pattern means NO route changes are needed.
Every route uses CurrentUser from core/deps.py, which calls get_current_user().
"""

import uuid
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from core.settings import get_settings
from db.session import get_session
from models.user import UserProfile


def _decode_jwt(token: str) -> dict:
    """Decode and validate a JWT token. Raises HTTPException on failure."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    authorization: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_session),
) -> UserProfile:
    """
    Auth dependency. Tries JWT Bearer first, falls back to X-User-ID.
    Returns the authenticated UserProfile.
    """
    user_id: Optional[uuid.UUID] = None

    # 1. Try JWT Bearer token
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        payload = _decode_jwt(token)
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Token missing 'sub' claim")
        try:
            user_id = uuid.UUID(sub)
        except ValueError:
            raise HTTPException(status_code=401, detail="Token 'sub' is not a valid UUID")

    # 2. Fall back to legacy X-User-ID header
    elif x_user_id:
        try:
            user_id = uuid.UUID(x_user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="X-User-ID must be a valid UUID")

    else:
        raise HTTPException(
            status_code=401,
            detail="Missing authentication. Provide Authorization: Bearer <token> or X-User-ID header.",
        )

    result = await db.exec(select(UserProfile).where(UserProfile.user_id == user_id))
    user = result.first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user
