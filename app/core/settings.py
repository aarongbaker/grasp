"""
core/settings.py
Pydantic-settings from .env, cached with @lru_cache (reads once).

Two database URLs because LangGraph's PostgresSaver uses psycopg3 (sync)
directly, not through SQLAlchemy. FastAPI routes use asyncpg through
SQLAlchemy. These are different drivers with different URL schemes:
  asyncpg: postgresql+asyncpg://...
  psycopg3: postgresql://...    (or postgresql+psycopg://...)
Keeping them separate avoids driver confusion and connection pool collisions.
"""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel value for development. The lifespan hook raises RuntimeError if this
# is still set in production (app_env == "production") — forces explicit secret config.
_JWT_SECRET_DEFAULT = "change-me-in-production"

# Allowed CORS origins in development. The lifespan hook rejects production
# deploys that still use only these origins (would lock out the real frontend).
_DEV_ORIGINS = ["http://localhost:3000", "http://localhost:8501", "http://localhost:5173"]


class Settings(BaseSettings):
    # env_file=".env" loads a local dotenv file. extra="ignore" means unknown
    # env vars don't cause validation errors — safe for Docker environments
    # that inject many env vars beyond what GRASP needs.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── App ───────────────────────────────────────────────────────────────────
    app_env: str = "development"  # "development" or "production" — validated below
    log_level: str = "INFO"       # passed to logging.getLogger().setLevel()
    cors_allowed_origins: list[str] = _DEV_ORIGINS.copy()

    # ── Auth ──────────────────────────────────────────────────────────────────
    jwt_secret_key: str = _JWT_SECRET_DEFAULT  # MUST be overridden in production
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60    # access token lifetime
    jwt_refresh_expire_days: int = 7  # refresh token lifetime

    @property
    def jwt_secret_is_default(self) -> bool:
        # Used by lifespan hook to warn/fail on default secret in production.
        return self.jwt_secret_key == _JWT_SECRET_DEFAULT

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_app_env(cls, value: str) -> str:
        # Strip and lowercase so APP_ENV=Production and APP_ENV=production both work.
        # Strict allowlist — fails fast on typos like "prod" instead of "production".
        normalized = str(value).strip().lower()
        if normalized not in {"development", "production"}:
            raise ValueError('APP_ENV must be either "development" or "production"')
        return normalized

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def validate_cors_allowed_origins(cls, value):
        # In Docker/Railway, CORS_ALLOWED_ORIGINS is often set as a JSON string
        # like '["https://grasp.pages.dev"]'. Pydantic-settings handles the JSON
        # parse automatically, but an empty string would silently allow nothing.
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must be a JSON array string such as "
                    "'[\"http://localhost:5173\", \"http://localhost:8000\"]'"
                )
        return value

    # ── Databases ─────────────────────────────────────────────────────────────
    # FastAPI / SQLAlchemy — uses asyncpg driver for async SQLModel operations.
    database_url: str = "postgresql+asyncpg://grasp:grasp@localhost:5432/grasp"
    # LangGraph PostgresSaver — uses psycopg3 (sync) driver internally.
    # Different driver, different URL prefix — must NOT be the same as database_url.
    langgraph_checkpoint_url: str = "postgresql://grasp:grasp@localhost:5432/grasp"

    # Test databases — used by pytest fixtures to isolate test state.
    test_database_url: str = "postgresql+asyncpg://grasp:grasp@localhost:5432/grasp_test"
    test_langgraph_checkpoint_url: str = "postgresql://grasp:grasp@localhost:5432/grasp_test"

    # ── Redis / Celery ────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"          # used by rate limiter
    celery_broker_url: str = "redis://localhost:6379/0"  # Celery task queue
    celery_result_backend: str = "redis://localhost:6379/1"  # separate DB for results

    # ── LLM Providers ────────────────────────────────────────────────────────
    anthropic_api_key: str = ""  # required for generator, enricher, renderer nodes

    # ── RAG ───────────────────────────────────────────────────────────────────
    # Number of chunks retrieved per RAG query. Higher = more context, more tokens.
    # 5 balances enrichment quality vs prompt token cost.
    rag_retrieval_top_k: int = 5

    # ── Pipeline ──────────────────────────────────────────────────────────────
    celery_task_timeout: int = 600   # seconds before Celery raises SoftTimeLimitExceeded
    celery_worker_concurrency: int = 1  # solo worker only; higher concurrency is unsupported

    @model_validator(mode="after")
    def validate_celery_worker_contract(self) -> "Settings":
        # GRASP's worker memory/correctness budget assumes exactly one in-flight pipeline.
        # Reject conflicting env/config early so runtime imports cannot silently drift to prefork/4.
        if self.celery_worker_concurrency != 1:
            raise ValueError(
                "CELERY_WORKER_CONCURRENCY must be 1 because the checked-in worker contract is "
                "--pool=solo --concurrency=1"
            )
        return self

    # Test flag: set to "1" to simulate a dag_builder crash for checkpoint resume test.
    # Preserved here so tests can set it via os.environ without importing the node.
    simulate_interrupt: str = ""

    # ── Registration ──────────────────────────────────────────────────────────
    # When True, POST /auth/register requires a valid invite code.
    invite_codes_enabled: bool = False
    # Email address of the admin user (set in env). Admin routes check
    # user.email == admin_email to authorize privileged operations.
    admin_email: str = ""

    # ── Billing / Stripe ───────────────────────────────────────────────────────
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""
    stripe_checkout_success_url: str = "http://localhost:5173/account/billing/success"
    stripe_checkout_cancel_url: str = "http://localhost:5173/account/billing/cancel"
    stripe_portal_return_url: str = "http://localhost:5173/account"
    stripe_webhook_tolerance_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    # lru_cache makes this a singleton — settings are read from .env once at
    # first call and reused for the lifetime of the process. This is safe because
    # env vars don't change at runtime in production (set at container start).
    # Tests that need different settings should call get_settings.cache_clear()
    # before patching env vars.
    return Settings()
