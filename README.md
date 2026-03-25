# GRASP

**Generative Retrieval-Augmented Scheduling & Planning** — AI-powered multi-course meal planning that turns your cookbook collection into personalized, time-coordinated cooking schedules.

GRASP uses Claude for recipe generation, OpenAI embeddings + Pinecone for cookbook RAG, and a LangGraph state machine to orchestrate the full pipeline: generate recipes, enrich them with your cookbook knowledge, build dependency graphs, merge into a parallel schedule, and render a step-by-step timeline.

## Prerequisites

- **Python 3.12**
- **Docker** (for Postgres and Redis)

## Quick Start

GRASP supports two local workflows:

- **Host-run app**: run FastAPI/Streamlit on your machine, use Docker only for Postgres/Redis
- **Docker-run app**: run API, worker, Postgres, and Redis together in Docker Compose

### Option A — Host-run app

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd grasp

# 2. Create a virtual environment and install dependencies
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Copy the example env and fill in your API keys (see next section)
cp .env.example .env

# 4. Generate a JWT secret key and add it to .env
python -c "import secrets; print(secrets.token_urlsafe(64))"
# Copy the output into .env as JWT_SECRET_KEY=...

# 5. Start Postgres and Redis
docker compose up -d postgres redis

# 6. Start the API
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 7. In another shell, start the archived Streamlit UI if needed
.venv/bin/streamlit run scripts/streamlit_app.py
```

### Option B — Docker-run app

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd grasp

# 2. Copy the example env and fill in your API keys
cp .env.example .env

# 3. Generate a JWT secret key and add it to .env
python -c "import secrets; print(secrets.token_urlsafe(64))"
# Copy the output into .env as JWT_SECRET_KEY=...

# 4. Build and run the local stack
docker compose up --build
```

This starts:
- `app` → FastAPI on `http://localhost:8000`
- `worker` → Celery worker (`--pool=solo --concurrency=1`)
- `postgres` → local dev database on `localhost:5432`
- `redis` → local Redis on `localhost:6379`

> The checked-in `.env.example` is written for **host-run development** (`localhost` URLs). In Docker Compose, the `app` and `worker` services override those connection URLs to use Docker service names (`postgres`, `redis`) so the same `.env` still works locally.

## API Keys

GRASP requires three API keys. Add them to your `.env` file:

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

Run the full stack in containers:

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

### Option B: Archived Streamlit UI

```bash
.venv/bin/streamlit run scripts/streamlit_app.py
```

Go to the **"Ingest Cookbooks"** tab, upload your PDFs, and click **Ingest**. The UI shows progress and a summary when done.

> **Supported formats:** PDF files (scanned or digital). The OCR pipeline handles both.

## Generate Meal Schedules

Launch the archived Streamlit UI:

```bash
.venv/bin/streamlit run scripts/streamlit_app.py
```

In the **"Plan a Meal"** tab:

1. **Describe your meal** — e.g., "A rustic Italian dinner: handmade pasta with bolognese, arugula salad, and tiramisu"
2. **Set guest count, meal type, and occasion**
3. **Add dietary restrictions** if needed
4. **Configure your kitchen** — number of burners and oven racks (this affects scheduling)
5. Click **Run Pipeline**

The pipeline will generate recipes, enrich them with your cookbook knowledge, validate, build dependency graphs, merge into a parallel schedule, and render a timeline with prep-ahead and cook-day steps.

## Running Tests

```bash
# Unit tests (no API keys needed)
.venv/bin/python -m pytest tests/ -m "not integration" -v

# Full suite including integration tests (requires API keys)
.venv/bin/python -m pytest tests/ -v
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
├── scripts/            # Archived utilities (Streamlit UI, bulk ingestion, smoke test)
├── tests/              # Test suite
├── frontend/           # React frontend
├── docker-compose.yml  # Local Postgres + Redis + API + worker
└── docs/               # Deployment and project docs
```
