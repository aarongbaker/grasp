# GRASP

**Generative Retrieval-Augmented Scheduling & Planning** — AI-powered multi-course meal planning that turns your cookbook collection into personalized, time-coordinated cooking schedules.

GRASP uses Claude for recipe generation, OpenAI embeddings + Pinecone for cookbook RAG, and a LangGraph state machine to orchestrate the full pipeline: generate recipes, enrich them with your cookbook knowledge, build dependency graphs, merge into a parallel schedule, and render a step-by-step timeline.

## Prerequisites

- **Python 3.12**
- **Node.js 20+**
- **Docker** (for Postgres and Redis)

## Local environment contract

`cp .env.example .env` gives you the intended **development** defaults:

- `APP_ENV=development`
- localhost database / Redis URLs
- no `CORS_ALLOWED_ORIGINS` override required for local startup
- placeholder `JWT_SECRET_KEY=change-me-in-production` is allowed locally and rejected only in production

Development startup should not require shell overrides for `APP_ENV`, JWT, or CORS. Production-only values belong in deploy environments, not in your local `.env`.

The meal-planning and cookbook ingestion flows still require real provider keys:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `PINECONE_API_KEY`

The API can boot without those keys, but LLM / RAG workflows will fail until they are set.

## Quick Start

GRASP supports two local workflows:

- **Host-run app**: run FastAPI, Celery, and the Vite frontend on your machine; use Docker only for Postgres/Redis
- **Docker-run backend**: run API, worker, Postgres, and Redis in Docker Compose; run the Vite frontend separately if you want the current web UI

### Option A — Host-run app from the repo root

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

# 7. In another shell, start the worker if you need background pipeline execution
.venv/bin/celery -A app.workers.celery_app worker --pool=solo --concurrency=1 --loglevel=INFO

# 8. In another shell, start the frontend
npm --prefix frontend run dev
```

Local URLs:
- API: `http://localhost:8000`
- Frontend: `http://localhost:5173`

> The checked-in `.env.example` is written for **host-run development** (`localhost` URLs). In Docker Compose, the `app` and `worker` services override those connection URLs to use Docker service names (`postgres`, `redis`) so the same `.env` still works locally.

### Option B — Docker-run backend

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd grasp

# 2. Copy the example env and add API keys
cp .env.example .env

# 3. Build and run the backend stack
docker compose up --build

# 4. In another shell, start the frontend if you want the current web UI
npm --prefix frontend install
npm --prefix frontend run dev
```

This starts:
- `app` → FastAPI on `http://localhost:8000`
- `worker` → Celery worker (`--pool=solo --concurrency=1`)
- `postgres` → local dev database on `localhost:5432`
- `redis` → local Redis on `localhost:6379`

## API Keys

GRASP requires three API keys for the full pipeline. Add them to your `.env` file when you want LLM / RAG-backed features to work:

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-proj-...
PINECONE_API_KEY=pcsk_...
```

| Key | What it does | Where to get it |
|-----|-------------|-----------------|
| `ANTHROPIC_API_KEY` | Powers Claude for recipe generation, step enrichment, and schedule summaries | [console.anthropic.com](https://console.anthropic.com/) |
| `OPENAI_API_KEY` | Generates text embeddings for cookbook content (used by the RAG pipeline) | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `PINECONE_API_KEY` | Vector database that stores and retrieves cookbook embeddings | [app.pinecone.io](https://app.pinecone.io/) |

You'll also want to configure your Pinecone index:

```env
PINECONE_INDEX_NAME=grasp-cookbooks
PINECONE_ENVIRONMENT=us-east-1-aws
```

The remaining `.env` values (database URLs, Redis, Celery) can be left at their defaults for local development.

## Infrastructure

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
- **app** on port 8000 — FastAPI API server
- **worker** — Celery background worker with memory-safe local settings
- **Postgres** on port 5432 — stores users, sessions, and ingestion records
- **Redis** on port 6379 — Celery broker/result backend and rate-limit storage

Database migrations run automatically on app startup via Alembic. You can also run them manually in a host-run environment:

```bash
.venv/bin/alembic upgrade head
```

### Deployment notes

- **Railway API service**: start command `uvicorn app.main:app --host 0.0.0.0 --port ${PORT}`
- **Railway worker service**: start command `celery -A app.workers.celery_app worker --pool=solo --concurrency=1 --loglevel=INFO`
- **Cloudflare / separate frontend deploys**: set `CORS_ALLOWED_ORIGINS` to your Cloudflare-hosted frontend origin(s) as a JSON array string, and set `VITE_API_URL` in the frontend build environment to your Railway API base URL.
- Keep environment variables for deploy targets in the platform environment, not in your local `.env`.
- Full checklist: `docs/RAILWAY_DEPLOY_CHECKLIST.md`

## Ingest Your Cookbooks

Before generating meals, ingest your cookbook PDFs so the RAG pipeline can draw from your personal recipe collection. The ingestion pipeline OCRs each PDF, classifies the document type, chunks the content, and embeds it into Pinecone.

### Option A: Command Line (bulk ingestion)

```bash
# Ingest all PDFs in a folder
.venv/bin/python scripts/ingest_folder.py ~/path/to/your/cookbooks/
```

This will:
1. Auto-create a dev user (`dev@grasp.local`)
2. Process each PDF: OCR, classify, chunk, embed
3. Print a summary with page/chunk counts and your user ID

## Generate Meal Schedules

Run the API, worker, and frontend as described above, then open the Vite app in your browser:

```text
http://localhost:5173
```

From there you can:
1. Upload and browse cookbook recipes
2. Create a new session
3. Run the planning pipeline
4. Review schedule and results views

## Running Tests

```bash
# Unit tests (no API keys needed)
.venv/bin/python -m pytest tests/ -m "not integration" -v

# Full suite including integration tests (requires API keys)
.venv/bin/python -m pytest tests/ -v

# Frontend lint / build
npm --prefix frontend run lint
npm --prefix frontend run build
```

## Project Structure

```
grasp/
├── app/                # FastAPI app package
│   ├── api/            # Routes (health, users, sessions, ingest, auth)
│   ├── core/           # Settings, auth, dependency injection, status helpers
│   ├── db/             # SQLAlchemy / SQLModel session setup
│   ├── graph/          # LangGraph state machine & pipeline nodes
│   ├── ingestion/      # Cookbook ingestion pipeline (OCR, classify, chunk, embed)
│   ├── models/         # Pydantic/SQLModel data models
│   └── workers/        # Celery task workers
├── scripts/            # Archived utilities and bulk-ingestion helpers
├── tests/              # Test suite
├── frontend/           # React frontend
├── docker-compose.yml  # Local Postgres + Redis + API + worker
└── docs/               # Deployment and project docs
```
