"""
app/main.py
FastAPI application entry point with lifespan hook.

Lifespan hook runs at startup:
  1. Create Postgres tables (SQLModel)
  2. Initialise Pinecone client
  3. Build and compile the LangGraph graph with PostgresSaver

The compiled graph is stored as a module-level variable and accessed
via get_graph() by route handlers that need status_projection().
This avoids circular imports while keeping the graph a singleton.

V2 SSE streaming: the graph instance here also becomes the SSE event
source. The polling route's two-tier read becomes the SSE push logic.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

_graph = None
_checkpointer_cm = None


async def get_graph():
    """Returns the compiled LangGraph graph, initialising it lazily if needed."""
    global _graph, _checkpointer_cm

    if _graph is not None:
        return _graph

    from app.core.settings import get_settings
    from app.graph.graph import build_grasp_graph

    settings = get_settings()

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        _checkpointer_cm = AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_url)
        checkpointer = await _checkpointer_cm.__aenter__()
        await checkpointer.setup()
        _graph = build_grasp_graph(checkpointer)
        return _graph
    except Exception as e:
        if settings.app_env == "production":
            raise RuntimeError(
                "LangGraph checkpoint initialisation failed in production. "
                "Check LANGGRAPH_CHECKPOINT_URL and Postgres connectivity."
            ) from e

        logger.warning("LangGraph init failed (%s). Using MemorySaver fallback.", e)
        from langgraph.checkpoint.memory import MemorySaver

        _graph = build_grasp_graph(MemorySaver())
        return _graph


async def ensure_checkpoint_tables() -> None:
    """Create LangGraph checkpoint tables if missing."""
    from app.core.settings import get_settings

    settings = get_settings()
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_url) as checkpointer:
        await checkpointer.setup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _checkpointer_cm

    # ── 0. Validate JWT secret ────────────────────────────────────────────────
    from app.core.settings import get_settings

    settings = get_settings()
    if settings.jwt_secret_is_default:
        if settings.app_env == "production":
            raise RuntimeError(
                "JWT_SECRET_KEY must be set to a strong random value in production. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        logger.warning(
            "JWT_SECRET_KEY is using the default value. Set JWT_SECRET_KEY in .env before deploying to production."
        )

    # ── 0b. Validate CORS origins ──────────────────────────────────────────────
    _DEV_ORIGINS = {"http://localhost:3000", "http://localhost:8501"}
    if settings.app_env == "production":
        configured_origins = set(settings.cors_allowed_origins)
        if configured_origins == _DEV_ORIGINS or configured_origins <= _DEV_ORIGINS:
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS must be set to your production domain(s) when APP_ENV=production. "
                'Example: CORS_ALLOWED_ORIGINS=\'["https://grasp.pages.dev"]\''
            )
    elif set(settings.cors_allowed_origins) != _DEV_ORIGINS:
        logger.info("CORS origins: %s", settings.cors_allowed_origins)

    # ── 1. Migrations run in deploy/pre-deploy, not app startup ─────────────
    # ── 2. Initialise Pinecone ────────────────────────────────────────────────
    try:
        from pinecone import Pinecone

        from app.core.settings import get_settings

        settings = get_settings()
        if settings.pinecone_api_key:
            pc = Pinecone(api_key=settings.pinecone_api_key)
            app.state.pinecone = pc
    except Exception as e:
        logger.warning("Pinecone init failed (%s). Ingestion will not work.", e)

    # ── 3. Graph initialisation is lazy, but checkpoint tables must exist ───
    # This retires fresh-database failures where Celery reaches LangGraph before
    # any route has lazily initialised the PostgresSaver tables.
    try:
        await ensure_checkpoint_tables()
    except Exception as e:
        if settings.app_env == "production":
            raise RuntimeError(
                "LangGraph checkpoint table setup failed in production. "
                "Check LANGGRAPH_CHECKPOINT_URL and Postgres permissions."
            ) from e
        logger.warning("Checkpoint table setup failed (%s). Lazy graph init may fall back later.", e)

    # Graph compilation itself remains lazy to avoid blocking readiness on the
    # full LangGraph build path.

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
    from app.db.session import engine

    await engine.dispose()


app = FastAPI(
    title="GRASP",
    description="Generative Retrieval-Augmented Scheduling & Planning",
    version="1.6.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
from app.core.settings import get_settings as _get_settings

_settings = _get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Rate Limiting ────────────────────────────────────────────────────────────
def _redis_is_reachable(redis_url: str) -> bool:
    """Quick TCP check to see if Redis is accepting connections."""
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(redis_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


if _redis_is_reachable(_settings.redis_url):
    limiter = Limiter(key_func=get_remote_address, storage_uri=_settings.redis_url)
    logger.info("Rate limiter using Redis at %s", _settings.redis_url)
else:
    limiter = Limiter(key_func=get_remote_address)  # in-memory fallback
    logger.warning(
        "Redis not reachable at %s. Rate limiter using in-memory storage — limits will not be shared across workers.",
        _settings.redis_url,
    )
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"},
    )


# ── Register routers ──────────────────────────────────────────────────────────
from app.api.routes.auth import router as auth_router
from app.api.routes.health import router as health_router
from app.api.routes.ingest import router as ingest_router
from app.api.routes.sessions import router as sessions_router
from app.api.routes.users import router as users_router

app.include_router(auth_router, prefix="/api/v1")
app.include_router(health_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(ingest_router, prefix="/api/v1")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
