# External Integrations

**Analysis Date:** 2026-03-18

## APIs & External Services

**LLM Providers:**
- Anthropic Claude - Primary LLM for menu generation, recipe enrichment, and scheduling
  - SDK/Client: `anthropic==0.84.0`
  - Auth: `ANTHROPIC_API_KEY` (environment variable)
  - Files: `core/llm.py` (retry logic), `graph/nodes/generator.py`, `graph/nodes/renderer.py`, `graph/nodes/enricher.py`

- OpenAI GPT - Secondary LLM for enrichment (vision capability for recipe analysis)
  - SDK/Client: `openai==2.26.0`
  - Auth: `OPENAI_API_KEY` (environment variable)
  - Files: `graph/nodes/enricher.py`

## Data Storage

**Databases:**

- PostgreSQL 16 (Development)
  - Connection: `DATABASE_URL=postgresql+asyncpg://grasp:grasp@localhost:5432/grasp`
  - Client: SQLAlchemy + asyncpg (async driver for FastAPI routes)
  - ORM: SQLModel (combines SQLAlchemy + Pydantic)
  - Checkpointing: Also uses psycopg3 driver at `LANGGRAPH_CHECKPOINT_URL` for LangGraph graph state persistence
  - Files: `db/session.py` (engine/session factory), `models/` directory (all SQLModel tables)

- PostgreSQL 16 (Testing)
  - Connection: `TEST_DATABASE_URL=postgresql+asyncpg://grasp:grasp@localhost:5432/grasp_test`
  - Isolated from development database
  - Separate port (5433) to avoid conflicts

**Vector Store:**
- Pinecone (managed vector database)
  - Purpose: RAG backend for recipe retrieval and similarity search
  - Client: `pinecone==4.1.0` (v4 API)
  - Index: `PINECONE_INDEX_NAME=grasp-cookbooks`
  - Environment: `PINECONE_ENVIRONMENT=us-east-1-aws`
  - Auth: `PINECONE_API_KEY` (environment variable)
  - Files: `ingestion/embedder.py` (vector generation), `graph/nodes/enricher.py` (retrieval)

**File Storage:**
- Local filesystem only - No external file storage integration
- PDF ingestion: Uploaded files are processed locally via `pymupdf` for OCR
- Files: `api/routes/ingest.py` (file upload handler), `ingestion/` (processing pipeline)

**Caching:**
- Redis 7 (local development, also production-capable)
  - Purpose: Celery task queue broker and result backend
  - Connection: `REDIS_URL=redis://localhost:6379/0`
  - Broker: `CELERY_BROKER_URL=redis://localhost:6379/0` (DB 0)
  - Result Backend: `CELERY_RESULT_BACKEND=redis://localhost:6379/1` (DB 1)
  - Files: `workers/celery_app.py` (Celery configuration)

## Authentication & Identity

**Auth Provider:**
- Custom JWT-based implementation
  - Implementation: Token-based authentication via FastAPI dependencies
  - Signing: PyJWT with HS256 algorithm
  - Secret: `JWT_SECRET_KEY` (must be strong random value in production)
  - Expiration: `JWT_EXPIRE_MINUTES=60` (access token), `JWT_REFRESH_EXPIRE_DAYS=7` (refresh token)
  - Password hashing: bcrypt (bcrypt==5.0.0)
  - Files: `api/routes/auth.py` (login/register), `core/deps.py` (FastAPI dependency for current user)

## Monitoring & Observability

**Error Tracking:**
- None detected - No Sentry, Datadog, or other error tracking service integration

**Logs:**
- Structured logging via structlog
  - JSON output in production (structured, machine-readable)
  - Pretty-printed output in development
  - Log level: Configurable via `LOG_LEVEL` environment variable
  - Context binding: Session ID correlation via `structlog.contextvars`
  - Files: `core/logging.py` (setup), bound in `core/deps.py` per request
  - Third-party loggers silenced: httpx, httpcore, openai, anthropic (set to WARNING level)

## CI/CD & Deployment

**Hosting:**
- Not detected in codebase analysis - No cloud provider SDK integration (no boto3, gcloud, etc.)
- Likely deployed manually or via simple container orchestration

**CI Pipeline:**
- GitHub Actions (metadata in `.github/` directory, but workflow files not analyzed)
- No test runner hooks detected in requirements

**Local Development:**
- Docker Compose (`docker-compose.yml`) orchestrates PostgreSQL (dev + test), Redis
- Streamlit app available as optional dev UI (`streamlit==1.55.0`)

## Environment Configuration

**Required env vars:**
- `ANTHROPIC_API_KEY` - Claude API key (starts with sk-ant-)
- `OPENAI_API_KEY` - GPT API key (starts with sk-proj-)
- `PINECONE_API_KEY` - Pinecone API key
- `DATABASE_URL` - PostgreSQL asyncpg URL
- `LANGGRAPH_CHECKPOINT_URL` - PostgreSQL psycopg URL (for graph checkpointing)
- `REDIS_URL` - Redis connection string
- `CELERY_BROKER_URL` - Redis broker URL
- `CELERY_RESULT_BACKEND` - Redis result backend URL
- `JWT_SECRET_KEY` - Strong random value for JWT signing

**Secrets location:**
- `.env` file (local development - never committed, see `.gitignore`)
- See `.env.example` for template
- Production: Injected as environment variables at runtime (standard 12-factor app approach)

## Webhooks & Callbacks

**Incoming:**
- None detected - Application is purely REST API based, no webhook receivers

**Outgoing:**
- None detected - No outgoing webhook implementations
- Celery tasks are internal job processing (not webhooks to external systems)

## Rate Limiting

**Per-route limiting:**
- slowapi 0.1.9 provides per-route rate limiting
- Example: `POST /sessions` limited to 30 requests per minute
- Files: `api/routes/sessions.py` (limiter decorator)

## Retry & Resilience

**LLM API Retries:**
- Transient errors automatically retried with exponential backoff
- Retryable exceptions: API timeout, connection error, rate limit, internal server error
- Max attempts: 3
- Backoff: exponential with multiplier=1, min=2s, max=30s
- No automatic retry for validation/auth errors (prevents amplifying costs)
- Files: `core/llm.py` (retry decorator using tenacity)

**Task Queue Retries:**
- Celery: NO automatic retry (task_max_retries=0)
- Rationale: Failed runs must be manually inspected to avoid amplifying LLM costs on systematic failures
- Task timeout: 600 seconds (10 minutes)
- Workers: 4 concurrent (configurable via CELERY_WORKER_CONCURRENCY)
- Files: `workers/celery_app.py` (configuration)

---

*Integration audit: 2026-03-18*
