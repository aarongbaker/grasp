"""
app/main.py
FastAPI application entry point with lifespan hook.

Lifespan hook runs at startup:
  1. Validate JWT secret — refuse to start production with the default placeholder
  2. Validate CORS origins — refuse production deploys with only dev origins
  3. Ensure LangGraph checkpoint tables exist — avoids Celery startup race condition

The compiled graph is stored as a module-level variable and accessed
via get_graph() by route handlers that need status_projection().
This avoids circular imports while keeping the graph a singleton.

Graph initialisation is LAZY (deferred past startup) to avoid blocking
readiness on the full LangGraph compilation path. Only checkpoint TABLE
setup happens at startup — the graph itself compiles on first get_graph() call.

Rate limiting: if Redis is reachable at startup, uses Redis for distributed
rate limits (shared across workers). Falls back to in-memory if Redis is down
at startup — in-memory limits are NOT shared across workers, so the effective
rate limit is multiplied by worker count in that case.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.core.logging import setup_logging

# setup_logging() must run BEFORE any logger.X calls anywhere in the process.
# It configures structlog+stdlib with the correct renderer (JSON in production,
# pretty-print in development). Calling it here at import time means it runs
# before any of the routers below are imported.
setup_logging()
logger = logging.getLogger(__name__)

# Module-level singletons for the LangGraph graph and its checkpointer context
# manager. Both are None until get_graph() is called for the first time.
# _checkpointer_cm is kept so it can be properly closed at shutdown.
_graph = None
_checkpointer_cm = None


async def get_graph():
    """Returns the compiled LangGraph graph, initialising it lazily if needed.

    Called by status_projection() and get_session_results() in routes/sessions.py.
    On first call: builds AsyncPostgresSaver, calls setup() to create tables,
    compiles the graph, and stores both as module-level singletons.

    If Postgres is unavailable and app_env != 'production', falls back to
    MemorySaver. This lets development work without a running Postgres instance
    but is explicitly rejected in production — MemorySaver loses state on restart.
    """
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
    """Create LangGraph checkpoint tables if missing.

    Called at startup to avoid the race where Celery submits a task before
    the first get_graph() call has created the checkpoint tables. Without this,
    fresh database deploys would fail the first pipeline run.

    This is a separate path from get_graph() because we want to ensure tables
    exist at startup WITHOUT triggering full graph compilation (which is lazy).
    AsyncPostgresSaver is used as a context manager so its connection pool
    is properly closed after setup.
    """
    from app.core.settings import get_settings

    settings = get_settings()
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_url) as checkpointer:
        await checkpointer.setup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager. Runs at startup and shutdown.

    The yield separates startup (before) from shutdown (after).
    All startup checks run before the server begins accepting requests.
    """
    global _checkpointer_cm

    # ── 0. Validate JWT secret ────────────────────────────────────────────────
    # The default JWT secret is a known public value. Signing tokens with it is
    # equivalent to no authentication — any attacker can forge tokens.
    # We warn in development (common enough to break the DX if we error) and
    # raise in production where security is non-negotiable.
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
    # If CORS_ALLOWED_ORIGINS still contains only localhost dev URLs in production,
    # real users would be blocked by CORS and the app would be unusable.
    # We check that production origins are a proper superset of dev origins.
    from app.core.settings import _DEV_ORIGINS

    dev_origins = set(_DEV_ORIGINS)
    configured_origins = set(settings.cors_allowed_origins)
    if settings.app_env == "production":
        if configured_origins == dev_origins or configured_origins <= dev_origins:
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS must be set to your production domain(s) when APP_ENV=production. "
                'Example: CORS_ALLOWED_ORIGINS=\'["https://grasp.pages.dev"]\''
            )
    elif configured_origins != dev_origins:
        logger.info("CORS origins: %s", settings.cors_allowed_origins)

    # ── 1. Migrations run in deploy/pre-deploy, not app startup ─────────────
    # SQLModel create_all is NOT called here. Schema changes must go through
    # explicit Alembic migrations run before the app deploys. create_all is
    # left to tests only, where it's safe to recreate schema from scratch.

    # ── 2. Graph initialisation is lazy, but checkpoint tables must exist ───
    # This retires fresh-database failures where Celery reaches LangGraph before
    # any route has lazily initialised the PostgresSaver tables. Without this,
    # the first pipeline run on a fresh deploy would fail with "table not found".
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
    # full LangGraph build path. The first status_projection() call will trigger it.

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    # Close the LangGraph checkpointer connection pool (if it was initialised).
    # Without this, the process would hang waiting for open connections to close.
    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
    # Dispose the SQLAlchemy engine — closes all pooled asyncpg connections.
    from app.db.session import engine

    await engine.dispose()


app = FastAPI(
    title="GRASP",
    description="Generative Retrieval-Augmented Scheduling & Planning",
    version="1.6.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Configured at module import time (not lifespan) because FastAPI middleware
# must be added before the app starts handling requests. get_settings() is safe
# at import time because it uses @lru_cache and reads from .env once.
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
# Limiter construction and 429 handler live in app/core/rate_limit.py so that
# route modules can import the same limiter instance (avoiding in-memory
# duplicates that are invisible to app.state.limiter).
from app.core.rate_limit import limiter, rate_limit_exceeded_handler

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


# ── Register routers ──────────────────────────────────────────────────────────
# All routers use /api/v1 prefix. Route-level prefixes (e.g. /sessions, /auth)
# are defined in each router module. Importing here (at module bottom) avoids
# circular imports — routers import from app.core.deps which imports from app.db,
# which is fully initialised by the time these imports run.
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.authored_recipes import router as authored_recipes_router
from app.api.routes.billing import router as billing_router
from app.api.routes.catalog import router as catalog_router
from app.api.routes.health import router as health_router
from app.api.routes.recipe_cookbooks import router as recipe_cookbooks_router
from app.api.routes.sessions import router as sessions_router
from app.api.routes.users import router as users_router

app.include_router(auth_router, prefix="/api/v1")
app.include_router(health_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(authored_recipes_router, prefix="/api/v1")
app.include_router(recipe_cookbooks_router, prefix="/api/v1")
app.include_router(catalog_router, prefix="/api/v1")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
