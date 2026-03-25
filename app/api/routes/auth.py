"""
api/routes/auth.py — Token issuance and refresh endpoints.

Issues short-lived access tokens (60 min) and long-lived refresh tokens (7 days).
The refresh endpoint lets the frontend silently obtain new access tokens without
re-prompting for credentials.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlmodel import select

from app.core.deps import DBSession
from app.core.settings import get_settings
from app.models.user import UserProfile

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/auth")


class TokenRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


def _build_access_token(user_id: str, email: str, settings) -> tuple[str, int]:
    """Build an access token. Returns (token, expires_in_seconds)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "type": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, settings.jwt_expire_minutes * 60


def _build_refresh_token(user_id: str, settings) -> str:
    """Build a long-lived refresh token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_expire_days),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@router.post("/token", response_model=TokenResponse)
@limiter.limit("5/minute")
async def issue_token(request: Request, body: TokenRequest, db: DBSession):
    """
    Issue a JWT for a registered user. Validates email + password.
    Returns access_token and refresh_token.
    """
    settings = get_settings()

    result = await db.exec(select(UserProfile).where(UserProfile.email == body.email))
    user = result.first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.password_hash or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token, expires_in = _build_access_token(str(user.user_id), user.email, settings)
    refresh_token = _build_refresh_token(str(user.user_id), settings)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")
async def refresh_token(request: Request, body: RefreshRequest, db: DBSession):
    """
    Exchange a valid refresh token for a new access + refresh token pair.
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            body.refresh_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired — please sign in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing 'sub' claim")

    # Verify user still exists
    import uuid
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID in token")

    result = await db.exec(select(UserProfile).where(UserProfile.user_id == uid))
    user = result.first()
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")

    access_token, expires_in = _build_access_token(str(user.user_id), user.email, settings)
    new_refresh_token = _build_refresh_token(str(user.user_id), settings)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=expires_in,
    )
