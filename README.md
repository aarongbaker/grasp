# GRASP

**Generative Retrieval-Augmented Scheduling & Planning** — AI-powered multi-course meal planning that turns your cookbook collection into personalized, time-coordinated cooking schedules.

GRASP uses Claude for recipe generation, OpenAI embeddings + Pinecone for cookbook RAG, and a LangGraph state machine to orchestrate the full pipeline: generate recipes, enrich them with your cookbook knowledge, build dependency graphs, merge into a parallel schedule, and render a step-by-step timeline.

## Prerequisites

- **Python 3.12**
- **Docker** (for Postgres and Redis)

## Quick Start

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

# 6. Ingest your cookbooks (see "Ingest Your Cookbooks" below)

# 7. Launch the UI
.venv/bin/streamlit run streamlit_app.py
```

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

Start the local services:

```bash
docker compose up -d postgres redis
```

This launches:
- **Postgres** on port 5432 — stores user profiles, sessions, and ingested book records
- **Redis** on port 6379 — Celery task broker (used by the FastAPI server, not needed for Streamlit)

Database migrations run automatically on app startup via Alembic. You can also run them manually:

```bash
.venv/bin/alembic upgrade head
```

## Ingest Your Cookbooks

Before generating meals, ingest your cookbook PDFs so the RAG pipeline can draw from your personal recipe collection. The ingestion pipeline OCRs each PDF, classifies the document type, chunks the content, and embeds it into Pinecone.

### Option A: Command Line (bulk ingestion)

```bash
# Ingest all PDFs in a folder
.venv/bin/python ingest_folder.py ~/path/to/your/cookbooks/
```

This will:
1. Auto-create a dev user (`dev@grasp.local`)
2. Process each PDF: OCR, classify, chunk, embed
3. Print a summary with page/chunk counts and your user ID

### Option B: Streamlit UI

```bash
.venv/bin/streamlit run streamlit_app.py
```

Go to the **"Ingest Cookbooks"** tab, upload your PDFs, and click **Ingest**. The UI shows progress and a summary when done.

> **Supported formats:** PDF files (scanned or digital). The OCR pipeline handles both.

## Generate Meal Schedules

Launch the Streamlit UI:

```bash
.venv/bin/streamlit run streamlit_app.py
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
├── graph/              # LangGraph state machine & pipeline nodes
│   ├── graph.py        # Graph topology
│   └── nodes/          # generator, enricher, validator, dag_builder, dag_merger, renderer
├── models/             # Pydantic/SQLModel data models
├── ingestion/          # Cookbook ingestion pipeline (OCR, classify, chunk, embed)
├── api/                # FastAPI routes (health, users, sessions, ingest)
├── core/               # Settings, auth, dependency injection
├── workers/            # Celery task workers
├── tests/              # Test suite
├── main.py             # FastAPI entry point
├── streamlit_app.py    # Interactive test UI
├── ingest_folder.py    # Bulk cookbook ingestion CLI
└── docker-compose.yml  # Postgres + Redis
```
