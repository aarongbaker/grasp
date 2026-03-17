"""
main.py
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

from core.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

_graph = None


def get_graph():
    """Returns the compiled LangGraph graph. Raises if not initialised."""
    if _graph is None:
        raise RuntimeError("LangGraph graph not initialised. Is the app running?")
    return _graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph

    # ── 0. Validate JWT secret ────────────────────────────────────────────────
    from core.settings import get_settings

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

    # ── 1. Run database migrations ────────────────────────────────────────────
    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    # ── 2. Initialise Pinecone ────────────────────────────────────────────────
    try:
        from pinecone import Pinecone

        from core.settings import get_settings

        settings = get_settings()
        if settings.pinecone_api_key:
            pc = Pinecone(api_key=settings.pinecone_api_key)
            app.state.pinecone = pc
    except Exception as e:
        logger.warning("Pinecone init failed (%s). Ingestion will not work.", e)

    # ── 3. Build LangGraph graph ──────────────────────────────────────────────
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        from core.settings import get_settings
        from graph.graph import build_grasp_graph

        settings = get_settings()

        _checkpointer_cm = AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_url)
        checkpointer = await _checkpointer_cm.__aenter__()
        await checkpointer.setup()
        _graph = build_grasp_graph(checkpointer)
        app.state.graph = _graph
        app.state._checkpointer_cm = _checkpointer_cm  # prevent GC

    except Exception as e:
        logger.warning("LangGraph init failed (%s). Using MemorySaver fallback.", e)
        from langgraph.checkpoint.memory import MemorySaver

        from graph.graph import build_grasp_graph

        _graph = build_grasp_graph(MemorySaver())
        app.state.graph = _graph

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if hasattr(app.state, "_checkpointer_cm"):
        await app.state._checkpointer_cm.__aexit__(None, None, None)
    from db.session import engine

    await engine.dispose()


app = FastAPI(
    title="GRASP",
    description="Generative Retrieval-Augmented Scheduling & Planning",
    version="1.6.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
from core.settings import get_settings as _get_settings

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
from api.routes.auth import router as auth_router
from api.routes.health import router as health_router
from api.routes.ingest import router as ingest_router
from api.routes.sessions import router as sessions_router
from api.routes.users import router as users_router

app.include_router(auth_router, prefix="/api/v1")
app.include_router(health_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(ingest_router, prefix="/api/v1")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
