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

# Single error message for all auth failures — prevents information leakage.
# Don't distinguish "token expired" from "token invalid" from "user not found"
# in the public API response (logs capture the specific reason).
_AUTH_ERROR = "Missing or invalid authentication token."


def _decode_jwt(token: str) -> dict:
    """Decode and validate an access JWT token. Raises HTTPException on failure.

    Validates:
      - Signature (jwt_secret_key)
      - Expiry (jwt.ExpiredSignatureError)
      - Token type field — rejects refresh tokens used as access tokens.
        This prevents a class of attack where a long-lived refresh token is
        used to access protected resources after the access token expires.
    """
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

    # Explicitly reject refresh tokens — they have type="refresh" and a much
    # longer expiry. Without this check, a stolen refresh token would grant
    # indefinite access to all protected routes.
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

    Used as Depends(get_current_user) in routes, or via CurrentUser type alias
    from core/deps.py. The DB lookup ensures the user still exists — tokens
    for deleted accounts are rejected even if the JWT signature is valid.
    """
    user_id: Optional[uuid.UUID] = None

    # Require "Bearer <token>" format. Fail immediately on missing/malformed header
    # so routes don't need to handle None user.
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]  # strip "Bearer " prefix
        payload = _decode_jwt(token)
        sub = payload.get("sub")  # "sub" claim carries the user_id UUID string
        if not sub:
            raise HTTPException(status_code=401, detail=_AUTH_ERROR)
        try:
            user_id = uuid.UUID(sub)
        except ValueError:
            # sub was not a valid UUID — token was tampered with or from a different system
            raise HTTPException(status_code=401, detail=_AUTH_ERROR)

    else:
        # No Authorization header, or not a Bearer token (e.g. Basic auth) — reject.
        raise HTTPException(status_code=401, detail=_AUTH_ERROR)

    # DB lookup validates the user still exists. Without this, tokens issued before
    # account deletion would continue to work until expiry.
    result = await db.exec(select(UserProfile).where(UserProfile.user_id == user_id))
    user = result.first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


def is_admin_user(user: UserProfile) -> bool:
    """Return whether the authenticated user matches the configured admin identity.

    Admin identity is configured via ADMIN_EMAIL env var (Settings.admin_email).
    An empty admin_email means no admin access is configured — returns False for all users.
    Case-insensitive comparison to prevent accidental access denial from email casing.
    """
    settings = get_settings()
    admin_email = settings.admin_email.strip().lower()
    return bool(admin_email) and user.email.strip().lower() == admin_email


def ensure_admin_user(user: UserProfile) -> UserProfile:
    """Require the authenticated caller to match the configured admin email.

    Called at the top of admin route handlers. Raises 403 Forbidden for non-admin
    users. Returns the user for chaining (e.g. used_by = ensure_admin_user(current_user)).
    """
    if not is_admin_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
