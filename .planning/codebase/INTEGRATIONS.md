# External Integrations

**Analysis Date:** 2026-04-08

## APIs & External Services

**LLM Providers:**
- Anthropic Claude - Recipe generation, step enrichment, schedule summaries
  - SDK/Client: `langchain-anthropic==1.3.4`
  - Auth: `ANTHROPIC_API_KEY` (required for production)
  - Usage: Generator node generates raw recipes; Enricher node enriches steps; Renderer node creates summaries
  - Retry strategy: `tenacity` exponential backoff on transient errors (timeout, rate limit, 5xx)

- OpenAI - Text embeddings for RAG retrieval
  - SDK/Client: `langchain-openai==1.1.10`
  - Model: `text-embedding-3-small` (1536 dimensions)
  - Auth: `OPENAI_API_KEY` (required for cookbook enrichment)
  - Usage: Embeds recipe chunks for Pinecone ingestion; retrieves similar chunks during enrichment

## Data Storage

**Databases:**
- PostgreSQL 16 (asyncpg driver)
  - Connection: `DATABASE_URL=postgresql+asyncpg://...` (FastAPI routes, SQLAlchemy async)
  - Client: SQLModel + SQLAlchemy ORM
  - Tables: `user_profile`, `session`, `authored_recipe`, `recipe_cookbook`, `ingestion_job`, `book_record`, `page_cache`, `invite`
  - Migrations: Alembic schema management (`alembic/versions/`)
  - Deployment: Two separate database URLs per `.env.example`
    - `DATABASE_URL` (asyncpg scheme) for FastAPI
    - `LANGGRAPH_CHECKPOINT_URL` (psycopg3 scheme) for LangGraph PostgresSaver

- LangGraph Checkpoint (PostgreSQL)
  - Connection: `LANGGRAPH_CHECKPOINT_URL=postgresql://...` (psycopg3 sync driver)
  - Tables: Created by `AsyncPostgresSaver.setup()` at startup
  - Stores: Graph state snapshots for resumable long-running meal planning sessions
  - Fallback: MemorySaver if PostgreSQL unavailable (development only)

**Vector Store:**
- Pinecone (managed service)
  - Index: `PINECONE_INDEX_NAME` (default: `grasp-cookbooks`)
  - Environment: `PINECONE_ENVIRONMENT` (default: `us-east-1-aws`)
  - Auth: `PINECONE_API_KEY`
  - Isolation: Per-user chunks via `rag_owner_key` metadata filter
  - Chunk types stored: recipe, ingredient_list, narrative, technique, tip
  - Vector dimension: 1536 (OpenAI text-embedding-3-small)
  - Retrieval: Top K=5 similar chunks for each recipe (configurable via `RAG_RETRIEVAL_TOP_K`)
  - Graceful degradation: If Pinecone fails, enrichment proceeds LLM-only without context

**File Storage:**
- Local filesystem only (development)
- No cloud storage integration (S3, GCS, Azure Blob) — PDFs uploaded but not persisted long-term

**Caching:**
- Redis (message broker and caching layer)
  - Broker: `CELERY_BROKER_URL=redis://...` (Celery task queue)
  - Result backend: `CELERY_RESULT_BACKEND=redis://...` (task result persistence)
  - Rate limiting: `slowapi` uses Redis for distributed rate limit counters

## Authentication & Identity

**Auth Provider:**
- Custom JWT implementation (no OAuth/OIDC)
  - Token type: HS256 symmetric signing
  - Auth location: `app/core/auth.py`
  - Flow:
    1. `POST /api/v1/auth/login` — username/email + password → access token (60 min default)
    2. `POST /api/v1/auth/refresh` — refresh token → new access token (7 day expiry)
    3. All routes: `Authorization: Bearer <jwt>` header required
  - Secrets: `JWT_SECRET_KEY` (must be 64+ bytes in production)
  - Algorithm: `JWT_ALGORITHM` (hardcoded to HS256)
  - Password hashing: bcrypt (5 rounds, via `app/core/auth.py`)

**User Management:**
- Invite-gated registration (optional)
  - Flag: `INVITE_CODES_ENABLED` in settings
  - Admin email: `ADMIN_EMAIL` for invite generation
  - Model: `app/models/invite.py` — one-time invite codes

## Monitoring & Observability

**Error Tracking:**
- Not detected - errors logged to stdout/stderr, structured via `structlog`

**Logs:**
- Structured logging via `structlog` (app/core/logging.py)
  - Format: JSON in production, pretty-printed in development
  - Level: Configurable via `LOG_LEVEL` environment variable
  - Node failures: Logged as `NodeError` with `ErrorType` enum and error context

**Metrics:**
- Token usage tracking: `extract_token_usage()` in `app/core/llm.py` extracts input/output token counts from LLM responses
- No metrics aggregation (Prometheus, CloudWatch) detected

## CI/CD & Deployment

**Hosting:**
- Railway (inferred from documentation and .env.example comments)
  - Frontend: Cloudflare Pages (static hosting)
  - API: Railway container (Python 3.12)
  - Worker: Railway container (Celery worker via `--pool=solo --concurrency=1`)
- Docker container registry for API/Worker images

**CI Pipeline:**
- GitHub Actions (implied by `.github/` directory present)
- Pre-commit hooks (no explicit config detected)
- Database migrations: Run before deployment (separate from app startup)

**Database Migrations:**
- Alembic (synchronous migration runner)
- Executed before app startup in production
- Config: `alembic.ini`, migrations: `alembic/versions/`

## Environment Configuration

**Required env vars:**
- `ANTHROPIC_API_KEY` — Claude LLM access
- `OPENAI_API_KEY` — Embeddings model access
- `PINECONE_API_KEY` — Vector database access
- `DATABASE_URL` — PostgreSQL async connection (SQLAlchemy)
- `LANGGRAPH_CHECKPOINT_URL` — PostgreSQL sync connection (LangGraph)
- `REDIS_URL` — Message broker connection
- `CELERY_BROKER_URL` — Celery broker (same as REDIS_URL typically)
- `CELERY_RESULT_BACKEND` — Celery result backend (typically redis://.../:1)
- `JWT_SECRET_KEY` — Access token signing key (64+ bytes in production)

**Secrets location:**
- `.env` file (git-ignored, not committed)
- Environment variables (Docker/Railway/platform-provided)
- No secrets manager integration (AWS Secrets Manager, HashiCorp Vault) detected

## Webhooks & Callbacks

**Incoming:**
- Not detected

**Outgoing:**
- Not detected

## Cross-Service Communication

**Celery Task Workers:**
- Async background jobs for:
  - PDF ingestion (rasterize, classify, chunk, embed, upsert to Pinecone)
  - LangGraph graph execution (meal planning pipeline)
  - State finalization (marking sessions as complete)
- Task serialization: JSON (defined in `app/workers/celery_app.py`)
- Concurrency: `CELERY_WORKER_CONCURRENCY` (1 in production for memory constraints)
- Timeout: `CELERY_TASK_TIMEOUT` (600s default)
- Retry policy: No automatic retries (manual intervention required for failed jobs)

**LangGraph Execution Context:**
- Routes and workers both call `build_grasp_graph()` from `app/graph/graph.py`
- Graph is compiled once at startup (lazy initialization with singleton pattern)
- Checkpoints stored in PostgreSQL for resumable state across worker restarts

## Rate Limiting

**Implementation:**
- `slowapi` library (per-route decorators)
- Storage: Redis-backed (distributed) or in-memory fallback
- Health check: TCP connection attempt to Redis at startup
- Failure mode: Falls back to in-memory if Redis unreachable

---

*Integration audit: 2026-04-08*
