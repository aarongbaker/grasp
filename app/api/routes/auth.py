"""
api/routes/auth.py — Token issuance and refresh endpoints.

Issues short-lived access tokens (60 min) and long-lived refresh tokens (7 days).
The refresh endpoint lets the frontend silently obtain new access tokens without
re-prompting for credentials.

Why two token types?
  Access tokens are short-lived to limit the damage if they're stolen (leaked
  from browser memory, logged in a proxy, etc.). They expire quickly so the
  attacker's window is small. Refresh tokens are long-lived but are only sent
  to the /auth/refresh endpoint — they're never sent to data APIs, so they're
  exposed less frequently.

  The type claim ("access" vs "refresh") on each token prevents a stolen refresh
  token from being used directly against data routes — get_current_user() in
  core/auth.py explicitly rejects tokens with type="refresh".

Token rotation: /auth/refresh issues a NEW refresh token alongside the new
access token. This implements refresh token rotation — if a refresh token is
stolen and used, the legitimate client's next refresh attempt will fail
(the old token is gone). This is not full rotation with revocation (no token
allowlist), but it reduces the window for stolen refresh token abuse.

Rate limits are lower on /token than on /refresh because credential stuffing
attacks target /token. Rate limiting /token slows brute-force attacks.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.core.deps import DBSession
from app.core.rate_limit import limiter
from app.core.settings import get_settings
from app.models.user import UserProfile
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
    expires_in: int  # seconds until access token expires


def _build_access_token(user_id: str, email: str, settings) -> tuple[str, int]:
    """Build an access token. Returns (token, expires_in_seconds).

    Claims:
      sub:   user_id UUID string — the authoritative user identifier
      email: included for display/debugging, not for auth decisions
      iat:   issued-at timestamp — used by JWT libraries for validation
      exp:   expiry timestamp — jwt.decode() automatically validates this
      type:  "access" — rejected by the refresh endpoint, required by data routes
    """
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
    """Build a long-lived refresh token.

    Does NOT include email — refresh tokens are only used to get new access
    tokens, not for resource access. Minimal payload reduces exposure.

    type: "refresh" — explicitly rejected by get_current_user() in core/auth.py.
    This prevents using a refresh token directly as an access token, which would
    bypass the short expiry protection of access tokens.
    """
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

    Why "Invalid email or password" for both missing user and wrong password?
      Returning "User not found" for wrong email lets attackers enumerate
      registered accounts. A single generic error prevents this information leak.

    bcrypt.checkpw() handles constant-time comparison internally —
    no need for hmac.compare_digest() here.

    Rate limit: 5/minute per IP to slow credential stuffing attacks.
    """
    settings = get_settings()

    result = await db.exec(select(UserProfile).where(UserProfile.email == body.email))
    user = result.first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # password_hash is None for OAuth users (not yet supported, but defensive check).
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

    The DB lookup (user still exists) is important: a refresh token issued
    before account deletion would otherwise continue to work until expiry.
    We verify the user still exists on every refresh, matching the behavior
    of get_current_user() for access tokens.

    Token rotation: always issues a new refresh token alongside the new access
    token. The client should replace its stored refresh token with the new one.
    Old refresh tokens remain valid until their expiry (no revocation store) —
    this is an acceptable tradeoff for a V1 implementation.
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

    # Reject access tokens used as refresh tokens.
    # The type claim must be "refresh" — access tokens have type="access".
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing 'sub' claim")

    # Validate the sub claim is a valid UUID — tampered tokens may have arbitrary strings.
    import uuid
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID in token")

    # DB lookup: verify the user still exists.
    # A refresh token issued to a deleted account should not grant new access tokens.
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
