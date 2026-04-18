"""
core/rate_limit.py
Shared SlowAPI keying helpers for request-aware rate-limit policies.

Why a hybrid key function (user identity vs IP)?
  - IP-based limits are weak for authenticated APIs: a single IP may host
    many legitimate users (office NAT, university proxy). An attacker can
    also cycle IPs to bypass IP-only limits.
  - User-identity limits are stricter: each authenticated user gets their
    own bucket regardless of IP. This is the right policy for session creation.
  - Unauthenticated requests fall back to IP — they can't authenticate anyway.

The create_session_limit() dynamic limit function applies different rates
depending on whether the caller is authenticated:
  - Authenticated: 10 sessions/minute (generous for interactive use)
  - Unauthenticated: 5 sessions/minute (tighter to slow abuse)

This is implemented as a SlowAPI "dynamic limit" — SlowAPI calls
create_session_limit(key) to determine the limit string for each request,
after user_identity_or_ip_key(request) has already determined the bucket key.
"""

import logging
import socket
from urllib.parse import urlparse

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.auth import _decode_jwt
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

# Prefixes distinguish authenticated user buckets from IP buckets in the
# Redis key namespace. Without prefixes, a user_id that happens to look like
# an IP string would collide with an IP bucket.
AUTHENTICATED_USER_PREFIX = "user:"
UNAUTHENTICATED_IP_PREFIX = "ip:"

# Rate limits for session creation. Higher limit for authenticated users
# because they have established accounts and are more accountable.
CREATE_SESSION_AUTHENTICATED_LIMIT = "10/minute"
CREATE_SESSION_UNAUTHENTICATED_LIMIT = "5/minute"


def user_identity_or_ip_key(request: Request) -> str:
    """Prefer authenticated user identity and fall back to remote IP.

    SlowAPI calls this function to compute the rate-limit bucket key for
    each request. Authenticated users get their own sub-second bucket;
    unauthenticated requests share a per-IP bucket.

    JWT decoding here is intentionally lenient — we catch all exceptions
    and fall back to IP rather than raising HTTP 401. Rate limiting should
    not break on a malformed Authorization header; the actual auth dependency
    (get_current_user) raises 401 at the route level.

    The decoded payload's `sub` claim is the user_id UUID string. We use it
    directly (not a DB lookup) to keep this function fast — it's called on
    every rate-limited request.
    """
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        try:
            payload = _decode_jwt(token)
        except Exception:
            # Token is invalid/expired — fall through to IP-based key.
            # This is intentional: rate-limit on IP for unauthenticated requests,
            # even if they're sending a Bearer token.
            payload = None
        if payload is not None:
            sub = payload.get("sub")
            if isinstance(sub, str) and sub.strip():
                return f"{AUTHENTICATED_USER_PREFIX}{sub.strip()}"

    return f"{UNAUTHENTICATED_IP_PREFIX}{get_remote_address(request)}"


def _redis_is_reachable(redis_url: str) -> bool:
    """Quick TCP check to see if Redis is accepting connections.

    Used at startup to decide whether to use Redis-backed or in-memory
    rate limiting. In-memory limits are not shared across workers, so
    production deployments should always have Redis reachable.
    Timeout is short (2s) to not delay startup on network issues.
    """
    try:
        parsed = urlparse(redis_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


# Build the shared limiter with Redis if available, fall back to in-memory.
# Exported so route modules can bind decorators to the same instance as
# app.state.limiter (set in app/main.py).
_settings = get_settings()
if _redis_is_reachable(_settings.redis_url):
    limiter = Limiter(key_func=get_remote_address, storage_uri=_settings.redis_url)
    logger.info("Rate limiter using Redis at %s", _settings.redis_url)
else:
    limiter = Limiter(key_func=get_remote_address)  # in-memory fallback
    logger.warning(
        "Redis not reachable at %s. Rate limiter using in-memory storage — limits will not be shared across workers.",
        _settings.redis_url,
    )


async def rate_limit_exceeded_handler(request, exc):
    """Convert SlowAPI's RateLimitExceeded into a JSON 429 response.

    Without this handler, SlowAPI returns a plain-text 429 which breaks
    frontend clients that expect JSON errors.
    """
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=429, content={"detail": f"Rate limit exceeded: {exc.detail}"})


def create_session_limit(key: str) -> str:
    """Apply the locked hybrid policy for session creation.

    SlowAPI dynamic limit function — called with the key returned by
    user_identity_or_ip_key(). Returns a rate limit string that SlowAPI
    uses to enforce the correct bucket size for this request's identity.

    The key prefix encodes authentication status, so we don't need to
    re-decode the JWT here.
    """
    if key.startswith(AUTHENTICATED_USER_PREFIX):
        return CREATE_SESSION_AUTHENTICATED_LIMIT
    return CREATE_SESSION_UNAUTHENTICATED_LIMIT
