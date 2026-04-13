# GRASP

**Generative Retrieval-Augmented Scheduling & Planning** - a hosted web app for creating personalized, time-coordinated cooking schedules from meal descriptions.

GRASP uses Claude and a LangGraph-driven scheduling pipeline to:
- generate recipes from free-text menu intent
- enrich and validate the working plan
- build dependency-aware cooking timelines
- render a step-by-step schedule for service

## Use GRASP

GRASP is now primarily a **hosted website**, not a local-only tool.

In the hosted app, a user can:
1. Sign in
2. Describe the meal they want to prepare
3. Run the scheduling pipeline
4. Review the generated schedule and results

Historical cookbook ingestion infrastructure still exists in the repository as legacy/internal code, but it is not part of the active hosted product contract. The hosted product now promises cookbook support only through the platform catalog and user-owned authored-library lanes, not through user-managed upload flows, ingestion-worker setup, or cookbook-specific retrieval.

### Hosted architecture

Production GRASP runs as a three-surface system:

1. **Railway API service** - FastAPI at `/api/v1`
2. **Railway worker service** - Celery worker for session planning jobs
3. **Cloudflare Pages frontend** - the public web UI

Background work such as meal-planning execution happens asynchronously in the worker. The frontend polls the API for pipeline status, terminal results, and recoverable failure details while the backend handles generation, enrichment, validation, and scheduling off the request path.

## Production deployment contract

Required runtime/build surfaces:

- **Railway API start command**
  ```bash
  uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
  ```

- **Railway worker start command**
  ```bash
  celery -A app.workers.celery_app worker --pool=solo --concurrency=1 --loglevel=INFO
  ```

  Notes:
  - The worker must remain on `--pool=solo --concurrency=1` for current memory assumptions.
  - The checked-in Celery app sets `broker_connection_retry_on_startup=True` explicitly.

- **Cloudflare Pages build env**
  ```bash
  VITE_API_URL=https://<railway-api-host>
  ```

  Notes:
  - Set the API base URL only
  - No trailing slash
  - No `/api/v1` suffix
  - The frontend appends `/api/v1` itself

- **Railway API CORS env**
  ```bash
  CORS_ALLOWED_ORIGINS=["https://<your-pages-domain>"]
  ```

  This must be a JSON array string, not a bare URL.

### Production guardrails

When `APP_ENV=production`, the checked-in code enforces:
- `JWT_SECRET_KEY` may not use the development placeholder
- `CORS_ALLOWED_ORIGINS` may not remain on localhost defaults
- `LANGGRAPH_CHECKPOINT_URL` must point at reachable Postgres with a psycopg-compatible URL scheme
- API and worker must be deployed together against the same Postgres + Redis

### Production migrations

Migrations are **not** run by app startup anymore.
Run Alembic before promoting the new API/worker revision:

```bash
alembic upgrade head
```

Keep deploy-only environment variables in Railway / Cloudflare, not in local `.env` files.

Deployment references:
- quick contract: `docs/RAILWAY_DEPLOY_CHECKLIST.md`
- full walkthrough: `docs/RAILWAY_CLOUDFLARE_DEPLOY_GUIDE.md`

## Developer setup

The rest of this README is for contributors working on GRASP locally.

## Prerequisites

- **Python 3.12**
- **Node.js 20+**
- **Docker** (for Postgres and Redis in local development)

## Local environment contract

`cp .env.example .env` gives you the intended **development** defaults:

- `APP_ENV=development`
- localhost database / Redis URLs
- no `CORS_ALLOWED_ORIGINS` override required for local startup
- placeholder `JWT_SECRET_KEY=change-me-in-production` is allowed locally and rejected only in production

Development startup should not require shell overrides for `APP_ENV`, JWT, or CORS. Production-only values belong in deploy environments, not in your local `.env`.

If your existing `.env` still contains `APP_ENV=production` from prior deploy testing, the repo-root API command will fail fast with production CORS/JWT guards. Reset local startup to the documented development contract by re-copying `.env.example` or setting `.env` back to `APP_ENV=development`.

The meal-planning flow requires a real provider key:
- `ANTHROPIC_API_KEY`

The API can boot without that key, but recipe generation / planning workflows will fail until it is set.

## Local development quick start

GRASP supports two local workflows:

- **Host-run app**: run FastAPI, Celery, and the Vite frontend on your machine; use Docker only for Postgres/Redis
- **Docker-run backend**: run API, worker, Postgres, and Redis in Docker Compose; run the Vite frontend separately

### Option A - Host-run app from the repo root

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd grasp

# 2. Create a virtual environment and install backend dependencies
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Install frontend dependencies
npm --prefix frontend install

# 4. Copy the example env and add API keys for the flows you want to exercise
cp .env.example .env

# 5. Start Postgres and Redis
docker compose up -d postgres redis

# 6. Start the API
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 7. In another shell, start the worker
.venv/bin/celery -A app.workers.celery_app worker --pool=solo --concurrency=1 --loglevel=INFO

# 8. In another shell, start the frontend
npm --prefix frontend run dev
```

Local URLs:
- API: `http://localhost:8000`
- Frontend: `http://localhost:5173`

> The checked-in `.env.example` is written for **host-run development** (`localhost` URLs). In Docker Compose, the `app` and `worker` services override those connection URLs to use Docker service names (`postgres`, `redis`) so the same `.env` still works locally.

### Option B - Docker-run backend

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd grasp

# 2. Copy the example env and add API keys
cp .env.example .env

# 3. Build and run the backend stack
docker compose up --build

# 4. In another shell, start the frontend
npm --prefix frontend install
npm --prefix frontend run dev
```

This starts:
- `app` → FastAPI on `http://localhost:8000`
- `worker` → Celery worker (`--pool=solo --concurrency=1`)
- `postgres` → local dev database on `localhost:5432`
- `redis` → local Redis on `localhost:6379`

## API keys

GRASP requires one API key for the active hosted planning pipeline:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

| Key | What it does | Where to get it |
|-----|-------------|-----------------|
| `ANTHROPIC_API_KEY` | Powers Claude for recipe generation, step enrichment, and schedule summaries | [console.anthropic.com](https://console.anthropic.com/) |

Legacy ingestion modules may still reference additional provider variables in historical code paths, but they are not part of the supported hosted runtime contract documented here.

The remaining `.env` values can stay at their development defaults for local work.

## Local infrastructure

### Local host-run infrastructure

Start only Postgres and Redis:

```bash
docker compose up -d postgres redis
```

### Local full Docker stack

Run the backend stack in containers:

```bash
docker compose up --build
```

This launches:
- **app** on port 8000 - FastAPI API server
- **worker** - Celery background worker with memory-safe local settings
- **Postgres** on port 5432 - stores users and sessions
- **Redis** on port 6379 - Celery broker/result backend and rate-limit storage

For local/manual database upgrades:

```bash
.venv/bin/alembic upgrade head
```

## Generating meal schedules

In production, users create and run sessions through the hosted frontend.

In local development, run the API, worker, and frontend as described above, then open:

```text
http://localhost:5173
```

From there you can:
1. Describe your meal plan
2. Run the planning pipeline
3. Review schedule and results views

## Running tests

```bash
# Unit tests (no API keys needed)
.venv/bin/python -m pytest tests/ -m "not integration" -v

# Full suite including integration tests (requires API keys)
.venv/bin/python -m pytest tests/ -v

# Frontend lint / build
npm --prefix frontend run lint
npm --prefix frontend run build
```

## Project structure

```
grasp/
├── app/                # FastAPI app package
│   ├── api/            # Routes (health, users, sessions, auth, catalog)
│   ├── core/           # Settings, auth, dependency injection, status helpers
│   ├── db/             # SQLAlchemy / SQLModel session setup
│   ├── graph/          # LangGraph state machine & pipeline nodes
│   ├── ingestion/      # Historical internal ingestion infrastructure
│   ├── models/         # Pydantic/SQLModel data models
│   └── workers/        # Celery task workers
├── tests/              # Test suite
├── frontend/           # React frontend
├── docs/               # Deployment docs and archived milestone research
└── docker-compose.yml  # Local Postgres + Redis + API + worker
```

Local machine state such as `.venv/`, `node_modules/`, `.env`, and tool runtime scratch directories is intentionally excluded from the structure above.
tructure above.
ture above.
tructure above.
