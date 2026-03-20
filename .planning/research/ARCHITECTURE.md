# Architecture Patterns: GRASP Production Deployment

**Domain:** Multi-service Python/React app (FastAPI + Celery + PostgreSQL + Redis + React SPA)
**Researched:** 2026-03-19
**Confidence:** HIGH — based on direct codebase analysis + HIGH confidence platform knowledge

---

## Existing Architecture (Local Dev)

The docker-compose.yml currently manages only infrastructure — it does not containerize the application itself. Local dev runs the app processes directly on the host:

```
docker-compose: postgres (5432) + postgres-test (5433) + redis (6379)

Host processes:
  uvicorn main:app       → FastAPI + LangGraph singleton
  celery -A workers.tasks worker  → Celery worker (separate process)
  vite dev server (3000) → React SPA with /api proxy to :8000

External:
  Anthropic API (LLM calls from Celery worker)
  Pinecone (vector store, RAG enricher)
  OpenAI (embeddings for ingestion)
```

**Critical architectural fact:** The LangGraph graph singleton lives ONLY in the FastAPI process. Celery workers create their own graph instance per task invocation — they cannot share the FastAPI graph object. Both connect to the same PostgreSQL database for checkpoint storage. This is by design and documented in `workers/tasks.py`.

---

## Recommended Production Architecture

### Platform: Railway.app

**Why Railway over alternatives:**

| Platform | PostgreSQL + pgvector | Redis | Multiple Services | Free Tier | Notes |
|----------|----------------------|-------|-------------------|-----------|-------|
| Railway | Yes (Postgres plugin, pgvector supported) | Yes (Redis plugin) | Yes (multiple deployments per project) | $5 credit/month (sufficient for 2-5 users) | Best fit |
| Render | Yes (pgvector on paid tier only) | Yes | Yes | Free tier — pgvector not on free | pgvector blocker |
| Fly.io | Yes (volumes) | Yes (upstash) | Yes | Free allowance | More complex networking |
| Heroku | Yes (add-on) | Yes (add-on) | Limited | No free tier | Cost |

**Railway is the correct choice** because pgvector support is included in Railway's managed PostgreSQL plugin without needing a paid upgrade. Render's free PostgreSQL tier does not support pgvector, which is a hard blocker for GRASP's RAG enricher.

**Confidence:** MEDIUM — based on platform knowledge current as of August 2025. Verify Railway's pgvector availability before committing.

---

### Service Topology

```
Railway Project: grasp-production
├── api              (Python service — FastAPI + uvicorn)
├── worker           (Python service — Celery worker, same codebase)
├── postgres         (Railway plugin — PostgreSQL 16 + pgvector)
└── redis            (Railway plugin — Redis 7)

External:
├── frontend         (Cloudflare Pages — static React SPA)
├── Anthropic API    (external, key in Railway env vars)
├── Pinecone         (external, key in Railway env vars)
└── OpenAI API       (external, key in Railway env vars)
```

**Frontend is hosted separately** on Cloudflare Pages (free, unlimited bandwidth, automatic HTTPS, global CDN). This is simpler than serving static files from FastAPI and removes frontend from the per-service Railway compute budget.

---

## Component Boundaries

### Component 1: FastAPI (api service)

| Aspect | Detail |
|--------|--------|
| Entry point | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Responsibility | Auth, session CRUD, pipeline enqueue, status polling, results retrieval |
| LangGraph role | Hosts the graph singleton for status_projection() and checkpoint reads |
| Startup | Runs `alembic upgrade head` → Pinecone init → LangGraph setup |
| Communicates with | PostgreSQL (asyncpg), Redis (rate limiting), Celery broker (task dispatch) |

### Component 2: Celery Worker (worker service)

| Aspect | Detail |
|--------|--------|
| Entry point | `celery -A workers.celery_app worker --loglevel=info --concurrency=2` |
| Responsibility | Executes LangGraph pipeline, executes ingestion pipeline |
| LangGraph role | Creates its own graph + checkpointer per task (documented in tasks.py) |
| Communicates with | PostgreSQL (asyncpg + psycopg3), Redis (task queue), Anthropic API, Pinecone, OpenAI |
| Key constraint | Cannot import from `main.py` — shares only `core/`, `graph/`, `models/`, `workers/` |

### Component 3: React SPA (Cloudflare Pages)

| Aspect | Detail |
|--------|--------|
| Build | `npm run build` → `frontend/dist/` |
| Serving | Cloudflare Pages serves `dist/` statically |
| API calls | Direct fetch to Railway API service public URL (e.g. `https://grasp-api.railway.app`) |
| Auth | JWT stored in memory/localStorage, sent as `Authorization: Bearer` header |
| Config change needed | `VITE_API_URL` env var replaces the Vite dev proxy |

### Component 4: PostgreSQL (Railway plugin)

| Aspect | Detail |
|--------|--------|
| Version | PostgreSQL 16 with pgvector extension |
| Used by | FastAPI (asyncpg), Celery worker (asyncpg + psycopg3), Alembic migrations |
| LangGraph tables | checkpoint, checkpoint_blobs, checkpoint_writes — managed by PostgresSaver.setup() |
| App tables | Managed by Alembic (sessions, users, ingestion jobs, books) |

### Component 5: Redis (Railway plugin)

| Aspect | Detail |
|--------|--------|
| Used by | Celery broker (db 0), Celery result backend (db 1), slowapi rate limiting |
| No persistence required | Celery tasks are re-queued on restart; rate limit state is ephemeral |

---

## Data Flow Changes for Production

### Local dev vs Production: Key differences

| Concern | Local Dev | Production |
|---------|-----------|------------|
| Frontend → API | Vite proxy (`/api` → `localhost:8000`) | Direct HTTPS to Railway API URL |
| Service URLs | `localhost:PORT` | Railway internal DNS (`postgres.railway.internal`, `redis.railway.internal`) |
| CORS | `localhost:3000` | Cloudflare Pages domain (`https://grasp.pages.dev`) |
| Secrets | `.env` file | Railway environment variables |
| DB migrations | Manual `alembic upgrade head` | Auto-run in FastAPI lifespan (already implemented) |
| pgvector | Installed in dev postgres | Must be enabled on Railway postgres (one-time setup) |
| OCR | Apple Vision (macOS only) | Tesseract or disable PDF ingestion |

### Apple Vision OCR — Critical Platform Blocker

`requirements.txt` includes `pyobjc-framework-Vision` and `pyobjc-framework-Quartz` with `sys_platform == "darwin"` guards. These are macOS-only packages for Apple's Vision framework OCR.

On a Linux Docker container (Railway uses Linux), these imports will be skipped due to the platform guard, but the `rasterise_and_ocr_pdf` function will still attempt to call the Vision framework APIs at runtime.

**Resolution required before deployment:** Either install Tesseract on the worker container as a fallback OCR engine, or make PDF ingestion non-fatal (return empty chunks on OCR failure) until a cross-platform OCR solution is in place. For a 2-5 friend MVP, disabling the ingestion feature is acceptable.

---

## New Components Required

### 1. Dockerfile (API + Worker — same image)

A single Dockerfile builds one image used by both the `api` and `worker` Railway services. The start command differs between services:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system deps (psycopg3 binary requires libpq)
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command — overridden per-service in Railway
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Worker service start command in Railway:**
```
celery -A workers.celery_app worker --loglevel=info --concurrency=2
```

**Why one image:** Both services share the same Python codebase. A single image eliminates divergence risk and halves build time.

### 2. Frontend API URL configuration

Add to `frontend/src/config.ts`:
```typescript
export const API_BASE = import.meta.env.VITE_API_URL ?? '';
```

All `fetch('/api/...')` calls become `fetch(`${API_BASE}/api/...`)`. In dev, `VITE_API_URL` is unset and the Vite proxy handles routing. In production, set `VITE_API_URL=https://grasp-api.railway.app` in Cloudflare Pages env vars.

### 3. Production environment variables

Variables required in Railway (both `api` and `worker` services share these):

```
# Database (Railway injects these automatically for plugin services)
DATABASE_URL=postgresql+asyncpg://${{PGUSER}}:${{PGPASSWORD}}@${{PGHOST}}:${{PGPORT}}/${{PGDATABASE}}
LANGGRAPH_CHECKPOINT_URL=postgresql://${{PGUSER}}:${{PGPASSWORD}}@${{PGHOST}}:${{PGPORT}}/${{PGDATABASE}}

# Redis (Railway injects REDIS_URL for plugin services)
REDIS_URL=${{REDIS_URL}}
CELERY_BROKER_URL=${{REDIS_URL}}/0
CELERY_RESULT_BACKEND=${{REDIS_URL}}/1

# App
APP_ENV=production
JWT_SECRET_KEY=<generated — python -c "import secrets; print(secrets.token_urlsafe(64))">
CORS_ALLOWED_ORIGINS=["https://grasp.pages.dev"]

# External APIs
ANTHROPIC_API_KEY=<secret>
OPENAI_API_KEY=<secret>
PINECONE_API_KEY=<secret>
PINECONE_INDEX_NAME=grasp-cookbooks
PINECONE_ENVIRONMENT=us-east-1-aws
```

**Note:** `core/settings.py` uses the field names `database_url`, `redis_url`, etc. Railway injects `DATABASE_URL` (uppercase). Pydantic-settings reads env vars case-insensitively by default, so no code change is needed.

---

## Service Discovery

### Internal (Railway plugin → service)

Railway plugins (Postgres, Redis) are accessible via internal DNS from within the Railway project:

- PostgreSQL: `${{Postgres.DATABASE_URL}}` — Railway auto-injects to services that reference it
- Redis: `${{Redis.REDIS_URL}}` — same pattern

The app sees these as standard connection strings. No DNS resolution code needed.

### External (React SPA → FastAPI)

The React SPA runs in the user's browser — it cannot use Railway internal networking. It must call the FastAPI service's public Railway URL. This is set via `VITE_API_URL` at build time in Cloudflare Pages.

**Pattern:**
1. Railway API service gets a public URL (e.g. `https://grasp-api-production.railway.app`)
2. Set `VITE_API_URL=https://grasp-api-production.railway.app` in Cloudflare Pages build env
3. CORS on FastAPI must allow the Cloudflare Pages domain

---

## Database Migrations on Deploy

**Current state (already correct):** `main.py` lifespan hook runs `alembic upgrade head` at startup. This means migrations run automatically every time the API service restarts.

**Production implications:**
- Migrations run before the API accepts traffic (lifespan hooks block startup) — safe
- LangGraph checkpoint tables are set up via `checkpointer.setup()` in the same lifespan — safe
- Celery worker starts independently and may beat the API — the worker only writes to DB after the graph runs, giving the API time to complete migrations first. For safety, add a `wait-for-postgres` check in the worker entrypoint, or accept that the first task may fail on a cold deploy (it will be re-queued)
- pgvector extension must be enabled manually once: `CREATE EXTENSION vector;` on the Railway Postgres instance before first deploy

---

## Frontend Serving: Static vs Separate Hosting

**Decision: Cloudflare Pages (separate hosting), not FastAPI static serving.**

| Approach | Pros | Cons |
|----------|------|------|
| FastAPI `StaticFiles` | Single service, simpler CORS | Adds latency to static asset serving, wastes Railway compute on CDN-appropriate work, couples frontend deploy to backend deploy |
| Cloudflare Pages | Free, global CDN, instant cache invalidation, independent deploy | Requires CORS config, VITE_API_URL env var at build time |

For a React SPA with `vite build`, Cloudflare Pages is the correct choice. The build output is pure static files — there is no benefit to serving them from Python.

**Cloudflare Pages setup:**
- Build command: `npm run build`
- Build output directory: `dist`
- Root directory: `frontend/`
- Environment variable: `VITE_API_URL=https://grasp-api-production.railway.app`

---

## LangGraph Checkpoints in Production

**How PostgresSaver works in production (same as dev):**

The `AsyncPostgresSaver` stores checkpoint state as serialized blobs in four tables:
- `checkpoints` — checkpoint metadata per thread
- `checkpoint_blobs` — state values per channel
- `checkpoint_writes` — pending writes
- `checkpoint_migrations` — schema version tracking

In production, the same PostgreSQL database used for app data also stores LangGraph checkpoints. This is fine for 2-5 users — the checkpoint tables are small (a few hundred KB per session).

**Key production consideration:** `checkpointer.setup()` is called in the FastAPI lifespan. This is idempotent — it uses `CREATE TABLE IF NOT EXISTS` and only runs schema migrations it hasn't run before (tracked in `checkpoint_migrations`). Safe to call on every restart.

**Celery worker pattern (already correct):** Each `run_grasp_pipeline` task opens its own `AsyncPostgresSaver` context manager:
```python
async with AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_url) as checkpointer:
    graph = build_grasp_graph(checkpointer)
```
This is correct for production — the worker creates a fresh checkpointer connection per task, uses it, and disposes it. No connection pool sharing between FastAPI and worker processes.

**The `langgraph_checkpoint_url` uses psycopg3 format** (`postgresql://...`, not `postgresql+asyncpg://...`). Railway's injected `DATABASE_URL` uses the standard `postgresql://` format, which is correct for this field. The `database_url` field used by SQLAlchemy needs the `+asyncpg` variant — these must be set separately in Railway env vars.

---

## Patterns to Follow

### Pattern 1: One Image, Two Start Commands

Build a single Docker image. Railway services differentiate by start command.

**Why:** Celery worker imports from the same codebase as FastAPI. Maintaining two Docker images means the imports can drift. One image ensures the worker and API are always running identical code.

### Pattern 2: Migrations in API Lifespan, Not Pre-Deploy Hook

Alembic `upgrade head` runs in the FastAPI lifespan hook (already implemented). Do not add a separate Railway "pre-deploy" step.

**Why:** The lifespan hook has access to `core/settings.py` and Railway's injected env vars. A separate pre-deploy step would need to replicate that env setup. The current approach is already correct.

### Pattern 3: pgvector Extension — Manual One-Time Setup

Run `CREATE EXTENSION IF NOT EXISTS vector;` manually via Railway's Postgres shell before the first deploy.

**Why:** Alembic migrations don't manage extensions. The extension must exist before any table using `vector` column types is created. Running it manually once is safer than embedding it in a migration (which could fail if the extension is already present on some environments).

### Pattern 4: CORS Configured via Environment Variable

`cors_allowed_origins` is already a `list[str]` setting in `core/settings.py`. In production, set:
```
CORS_ALLOWED_ORIGINS=["https://grasp.pages.dev","https://custom-domain.com"]
```

Pydantic-settings parses JSON-encoded lists from environment variables natively. No code change needed.

### Pattern 5: JWT Secret Must Change

`main.py` already enforces this — it raises `RuntimeError` if `JWT_SECRET_KEY` is the default value and `APP_ENV=production`. Generate with:
```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Sharing Postgres Connection Pool Between FastAPI and Worker

**What goes wrong:** If the Celery worker were to import the FastAPI `db/session.py` engine singleton, it would share a connection pool with the FastAPI process. Async connection pools are not safe across forked processes.

**Current state:** Already correct — `workers/tasks.py` creates its own `create_async_engine` per task. Do not change this.

### Anti-Pattern 2: Running Alembic from the Worker

**What goes wrong:** The Celery worker starts concurrently with the API service. If both run `alembic upgrade head` simultaneously, the second run will attempt to apply migrations that the first already applied, potentially causing lock contention or errors.

**Prevention:** Only the API service runs migrations (in its lifespan hook). The worker assumes the schema is ready.

### Anti-Pattern 3: Hardcoding Railway URLs in Code

**What goes wrong:** Railway service URLs change on redeploy if services are deleted and recreated.

**Prevention:** Use Railway's reference variables (`${{Postgres.DATABASE_URL}}`) in env var definitions. Never put Railway hostnames in committed code.

### Anti-Pattern 4: Serving React SPA from FastAPI StaticFiles in Production

**What goes wrong:** Every static asset request consumes Railway compute. On the free tier, this exhausts the monthly credit faster and adds latency vs a CDN.

**Prevention:** Use Cloudflare Pages. Keep FastAPI serving only `/api/*` routes.

### Anti-Pattern 5: Two Postgres Instances (test DB) in Production

**What goes wrong:** `docker-compose.yml` runs `postgres-test` on port 5433 for test isolation. In production, this service must not exist — Railway only needs one Postgres plugin.

**Prevention:** The test database is only needed locally and in CI. Railway deploys only the production Postgres plugin.

---

## Build Order for Deployment Phases

### Phase 1: Infrastructure Setup (no code changes)
1. Create Railway project
2. Add PostgreSQL plugin — enable pgvector: `CREATE EXTENSION IF NOT EXISTS vector;`
3. Add Redis plugin
4. Verify plugin connection strings are available as Railway reference variables

### Phase 2: Docker Image
1. Write `Dockerfile` at project root
2. Test locally: `docker build -t grasp . && docker run -p 8000:8000 --env-file .env grasp`
3. Verify the API starts and migrations run cleanly in the container

### Phase 3: API Service Deploy
1. Connect Railway API service to GitHub repo
2. Set environment variables (DATABASE_URL variants, Redis URLs, API keys, JWT secret, CORS)
3. Set start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Deploy — verify `/api/v1/health` returns 200
5. Verify Alembic migrations ran (check Railway logs for "Running upgrade")

### Phase 4: Worker Service Deploy
1. Add second Railway service from same repo/image
2. Set same environment variables as API service (copy from API service)
3. Set start command: `celery -A workers.celery_app worker --loglevel=info --concurrency=2`
4. Deploy — verify worker connects to Redis broker (check logs for "celery@... ready")

### Phase 5: Frontend Deploy
1. Add `VITE_API_URL` export to `frontend/src/` config
2. Update all API fetch calls to use `${API_BASE}/api/...`
3. Connect Cloudflare Pages to GitHub repo, root: `frontend/`, build: `npm run build`, output: `dist`
4. Set `VITE_API_URL=https://[railway-api-url]` in Cloudflare Pages env vars
5. Build and deploy
6. Update Railway API service `CORS_ALLOWED_ORIGINS` to include the Cloudflare Pages URL
7. Redeploy API service to pick up new CORS config

### Phase 6: Validation
1. Register a user via the deployed frontend
2. Create a session, run the pipeline
3. Verify Celery task appears in Railway worker logs
4. Verify results appear in frontend when pipeline completes

---

## Scalability Considerations

For 2-5 concurrent users, the single-instance Railway deployment is entirely sufficient. No changes needed at this scale.

| Concern | At 2-5 users | At 50 users | At 500 users |
|---------|--------------|-------------|--------------|
| API | Single Railway service (512MB RAM) | Still single instance | Horizontal scaling |
| Celery | 2 concurrent workers | Increase concurrency or add workers | Multiple worker services |
| PostgreSQL | Railway free plan | Railway Pro plan | Read replicas |
| LangGraph checkpoints | Same DB as app data | Same (small footprint) | Separate DB |
| Redis | Railway free plan | Still free plan | Cluster |

---

## Sources

**Confidence levels:**

- Codebase analysis (`main.py`, `workers/tasks.py`, `core/settings.py`, `docker-compose.yml`, `requirements.txt`) — HIGH
- Railway pgvector support — MEDIUM (known as of August 2025; verify current availability)
- Render pgvector free tier limitation — MEDIUM (verify current tier structure)
- Cloudflare Pages pricing/availability — HIGH (free tier is well-established)
- LangGraph PostgresSaver behavior — HIGH (direct code analysis of the checkpoint pattern)
- Pydantic-settings JSON list env var parsing — HIGH (documented Pydantic v2 behavior)

**Official references to verify before implementation:**
- Railway Postgres plugin: https://docs.railway.app/databases/postgresql
- Railway pgvector: https://railway.app/template/pgvector (or check plugin docs)
- Cloudflare Pages: https://developers.cloudflare.com/pages/
- LangGraph checkpoint-postgres: https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres
