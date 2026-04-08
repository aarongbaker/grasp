"""
Shared SlowAPI keying helpers for request-aware rate-limit policies.
"""

from fastapi import Request
from slowapi.util import get_remote_address

from app.core.auth import _decode_jwt

AUTHENTICATED_USER_PREFIX = "user:"
UNAUTHENTICATED_IP_PREFIX = "ip:"
CREATE_SESSION_AUTHENTICATED_LIMIT = "10/minute"
CREATE_SESSION_UNAUTHENTICATED_LIMIT = "5/minute"


def user_identity_or_ip_key(request: Request) -> str:
    """Prefer authenticated user identity and fall back to remote IP."""
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        try:
            payload = _decode_jwt(token)
        except Exception:
            payload = None
        if payload is not None:
            sub = payload.get("sub")
            if isinstance(sub, str) and sub.strip():
                return f"{AUTHENTICATED_USER_PREFIX}{sub.strip()}"

    return f"{UNAUTHENTICATED_IP_PREFIX}{get_remote_address(request)}"


def create_session_limit(key: str) -> str:
    """Apply the locked hybrid policy for session creation."""
    if key.startswith(AUTHENTICATED_USER_PREFIX):
        return CREATE_SESSION_AUTHENTICATED_LIMIT
    return CREATE_SESSION_UNAUTHENTICATED_LIMIT
