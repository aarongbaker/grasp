"""
api/routes/auth.py — Token issuance endpoint.

V1: Issues JWT based on user_id (no password). Suitable for dev/internal use.
V2: Add proper password hashing or OAuth2 provider integration.
"""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from core.deps import DBSession
from core.settings import get_settings
from models.user import UserProfile

router = APIRouter(prefix="/auth")


class TokenRequest(BaseModel):
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/token", response_model=TokenResponse)
async def issue_token(body: TokenRequest, db: DBSession):
    """
    Issue a JWT for a registered user. V1 uses email-only (no password).
    Returns access_token with user_id as the 'sub' claim.
    """
    settings = get_settings()

    result = await db.exec(select(UserProfile).where(UserProfile.email == body.email))
    user = result.first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.user_id),
        "email": user.email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }

    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
    )
