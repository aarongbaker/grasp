"""
tests/test_middleware.py
Integration tests for CORS and rate limiting middleware.

Tests use a minimal FastAPI app with the same middleware configuration
as main.py but without external dependencies (no DB, no Redis, no Celery).
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ─────────────────────────────────────────────────────────────────────────────
# Test app factory
# ─────────────────────────────────────────────────────────────────────────────

def _create_middleware_app(
    allowed_origins: list[str] | None = None,
    rate_limit: str = "100/minute",
) -> FastAPI:
    """Create a minimal app with CORS and rate limiting configured."""
    app = FastAPI()

    # CORS
    origins = allowed_origins or ["http://localhost:3000"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiting (in-memory — no Redis needed for tests)
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded: {exc.detail}"},
        )

    @app.get("/test")
    @limiter.limit(rate_limit)
    async def test_endpoint(request: Request):
        return {"ok": True}

    return app


# ─────────────────────────────────────────────────────────────────────────────
# CORS tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cors_allowed_origin():
    """Requests from an allowed origin get CORS headers in the response."""
    app = _create_middleware_app(allowed_origins=["http://localhost:3000"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/test",
            headers={"Origin": "http://localhost:3000"},
        )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


@pytest.mark.asyncio
async def test_cors_disallowed_origin():
    """Requests from a non-allowed origin do NOT get CORS allow headers."""
    app = _create_middleware_app(allowed_origins=["http://localhost:3000"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/test",
            headers={"Origin": "http://evil.example.com"},
        )
    assert resp.status_code == 200
    # FastAPI's CORSMiddleware omits the header entirely for disallowed origins
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.asyncio
async def test_cors_preflight_allowed():
    """CORS preflight (OPTIONS) for allowed origin returns 200 with headers."""
    app = _create_middleware_app(allowed_origins=["http://localhost:3000"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.options(
            "/test",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "POST" in resp.headers.get("access-control-allow-methods", "")


@pytest.mark.asyncio
async def test_cors_preflight_disallowed():
    """CORS preflight from disallowed origin does not get allow headers."""
    app = _create_middleware_app(allowed_origins=["http://localhost:3000"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.options(
            "/test",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert "access-control-allow-origin" not in resp.headers


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiting tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_allows_under_limit():
    """Requests under the rate limit succeed."""
    app = _create_middleware_app(rate_limit="5/minute")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/test")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_returns_429_when_exceeded():
    """Exceeding the rate limit returns 429 with detail message."""
    app = _create_middleware_app(rate_limit="3/minute")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Send 3 requests (within limit)
        for _ in range(3):
            resp = await ac.get("/test")
            assert resp.status_code == 200

        # 4th request should be rate-limited
        resp = await ac.get("/test")
        assert resp.status_code == 429
        assert "Rate limit exceeded" in resp.json()["detail"]
