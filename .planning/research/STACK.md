# Technology Stack: Deployment for GRASP

**Project:** GRASP v1.1 — Production Deployment
**Researched:** 2026-03-19
**Scope:** Deployment tooling only. Application stack (FastAPI, React, PostgreSQL, Redis, Celery, LangGraph) is locked. This document covers containerization, hosting platforms, and infrastructure additions needed to reach a public URL.

---

## Research Note on Confidence

Web access was unavailable during this research session. Findings are based on training data through August 2025. Platform pricing and free-tier limits change frequently. **Every platform-specific limit marked LOW or MEDIUM confidence must be verified against the current pricing page before committing to a platform.** Links to authoritative sources are provided for each.

---

## Critical Pre-Deployment Finding: macOS-Only OCR Dependencies

Before selecting any hosting platform, address a blocker in the existing codebase.

The ingestion pipeline (`ingestion/rasteriser.py`) uses:
- `pyobjc-framework-Vision 11.0` — Apple Vision OCR framework
- `pyobjc-framework-Quartz 11.0` — macOS CGImage support

**These packages install and run only on macOS.** Any Linux-based container or cloud host will fail to install them. This affects `pip install -r requirements.txt` inside a Dockerfile built for Linux/amd64.

**Resolution options (choose one before containerizing):**

| Option | Effort | Impact |
|--------|--------|--------|
| Stub/skip ingestion in production | Low | OCR upload disabled, but meal planning pipeline works fully |
| Replace with Tesseract OCR (`pytesseract`) | Medium | Cross-platform OCR, slight quality drop vs. Apple Vision |
| Replace with `pymupdf` text extraction only (no vision) | Low | Works if PDFs have embedded text; fails on scanned pages |
| Docker multi-platform build with macOS runner | Very High | Not practical on free hosting |

**Recommendation:** Stub the ingestion endpoint for v1.1. The meal planning pipeline (the core feature) works without it. Document ingestion as macOS-dev-only for now. This unblocks containerization with zero application changes.

---

## Recommended Deployment Stack

### Overview

| Layer | Technology | Why |
|-------|-----------|-----|
| Containerization | Docker + Docker Compose (production variant) | Already in use for dev; same mental model for prod |
| Container registry | GitHub Container Registry (GHCR) | Free for public/private images with GitHub account |
| Hosting platform | **Railway** (primary recommendation) | pgvector support confirmed, Redis as first-class service, free trial then ~$5/mo hobby plan |
| Postgres | Railway-managed PostgreSQL | pgvector extension available, no manual extension install needed |
| Redis | Railway-managed Redis | First-class service, same dashboard |
| Secrets management | Platform environment variables (Railway dashboard) | Sufficient for 2-5 users; no Vault/SSM needed |
| Frontend serving | React build served via FastAPI static mount OR Caddy sidecar | Eliminates separate hosting for static assets |
| HTTPS/TLS | Provided automatically by Railway subdomain | No cert management needed |

---

## Recommended Stack — Detailed

### Containerization

**Docker** is already used for local development. For production, the additions are:

| Addition | Purpose | Notes |
|----------|---------|-------|
| `Dockerfile` (backend) | Build Python app image | Separate from docker-compose; needs `--platform linux/amd64` if building on Apple Silicon |
| `Dockerfile` (frontend) | Build React static assets | Multi-stage: Node build stage → copy `dist/` into backend image or Caddy |
| `.dockerignore` | Exclude `.venv`, `node_modules`, test files, local `.env` | Reduces image size significantly |
| Production `docker-compose.yml` | Orchestrate API + worker containers | Not needed on Railway (services deployed independently) but useful for single-VPS option |

**Multi-stage Dockerfile pattern (backend with bundled frontend):**

```dockerfile
# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend
FROM python:3.11-slim AS backend
WORKDIR /app
# Install system deps for psycopg binary
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
# Exclude macOS-only packages
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Copy built frontend into location FastAPI will serve
COPY --from=frontend-builder /app/frontend/dist ./static
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Note: `requirements.txt` must have `pyobjc-*` packages removed or guarded before this works. A `requirements.prod.txt` that excludes those packages is the cleanest solution.

**Confidence:** HIGH — Docker multi-stage builds are well-established; this is standard FastAPI deployment practice.

---

### Hosting Platform Comparison

Three platforms are viable for this stack. One is recommended; the others are documented for reference.

#### Option A: Railway (Recommended)

**URL:** https://railway.com

| Criterion | Detail |
|-----------|--------|
| pgvector | Available on Railway-managed Postgres (extension pre-installed or installable via `CREATE EXTENSION vector`) |
| Redis | First-class managed Redis service in Railway dashboard |
| Free tier | $5 credit on Hobby plan; ~$5/month thereafter for minimal usage |
| Sleep on inactivity | No — services stay up (unlike Render free tier) |
| Deploy method | GitHub repo connect + auto-deploy on push, or Railway CLI (`railway up`) |
| Multi-service | API service + Celery worker service + Postgres + Redis all in one project |
| Environment vars | Set per-service in dashboard; injected at runtime |
| Subdomain | `<app>.up.railway.app` — public HTTPS URL out of the box |
| Resource limits (Hobby) | 512 MB RAM per service, 1 vCPU shared |
| Storage | Postgres volume: 1 GB included; expandable |
| Confidence | MEDIUM — pgvector availability confirmed in community docs as of mid-2024; verify current extension support before committing |

**Why Railway over alternatives:** Redis + Postgres + API + worker can all live in one Railway project. No stitching together multiple platforms. The $5/month cost is the most predictable and lowest friction for a 2-5 user app that needs to stay awake.

**Verification needed:** Confirm `CREATE EXTENSION vector` works on current Railway Postgres tier. Check https://railway.com/pricing for current Hobby plan limits.

#### Option B: Render (Alternative)

**URL:** https://render.com

| Criterion | Detail |
|-----------|--------|
| pgvector | Available — Render Postgres is standard PostgreSQL 15/16; pgvector installable via `CREATE EXTENSION vector` |
| Redis | Available as managed Redis service (paid tier required for always-on Redis) |
| Free tier | Web services sleep after 15 min inactivity — problematic for Celery workers |
| Free Postgres | 90-day free trial, then ~$7/month for smallest paid tier |
| Free Redis | Free Redis expires after 30 days; then paid only |
| Deploy method | GitHub repo connect; auto-deploy on push |
| Subdomain | `<app>.onrender.com` — public HTTPS URL |
| Confidence | MEDIUM — free tier sleep behavior confirmed as of early 2025; verify current policy |

**Why not Render as primary:** The 15-minute sleep on free web services kills Celery workers and makes the app feel broken when a friend first opens it. The free Redis expires after 30 days. For a multi-service app that needs Redis + Postgres + workers, Render requires paid tiers for reliable operation, costing ~$14-20/month — more than Railway Hobby for worse DX.

**Use Render if:** You want managed Postgres with pgvector and are willing to pay $7/month for Postgres + $7/month for Redis while keeping the API free.

#### Option C: Fly.io (Alternative)

**URL:** https://fly.io

| Criterion | Detail |
|-----------|--------|
| pgvector | Available — Fly Postgres is self-managed PostgreSQL running on Fly VMs; pgvector installable but requires SSH/flyctl to run `CREATE EXTENSION vector` |
| Redis | Available via Upstash Redis (per-request pricing) or self-hosted Redis VM |
| Free tier | Eliminated in 2024 for new accounts — now requires credit card, $0/month only if within very tight resource limits |
| Deploy method | `flyctl deploy` CLI; Dockerfile-based |
| Subdomain | `<app>.fly.dev` — public HTTPS URL |
| Resource model | Pay per second of compute; machines sleep when no traffic unless configured otherwise |
| Complexity | Higher — requires managing Fly Postgres separately, running migrations via flyctl, SSH for extension setup |
| Confidence | LOW — Fly.io pricing changed significantly in 2024; free allowances may have changed further. Verify at https://fly.io/docs/about/pricing/ |

**Why not Fly.io as primary:** Higher operational complexity for a 2-5 user app. Fly Postgres is self-managed (not a fully managed service), which means manual extension setup, manual backups configuration, and more ops overhead. The free tier changes make cost unpredictable. Railway is simpler with equivalent capability.

**Use Fly.io if:** You're already comfortable with flyctl, want more control over the Postgres instance, or need to deploy in a specific geographic region.

---

### Secrets Management

For 2-5 users, platform environment variables are sufficient. No need for Vault, AWS SSM, or similar.

| Secret | Where to set | Notes |
|--------|-------------|-------|
| `ANTHROPIC_API_KEY` | Railway service env vars | Per-service; set on API service and Celery worker service |
| `OPENAI_API_KEY` | Railway service env vars | Same |
| `PINECONE_API_KEY` | Railway service env vars | Same |
| `PINECONE_INDEX_NAME` | Railway service env vars | |
| `JWT_SECRET_KEY` | Railway service env vars | Generate with `openssl rand -hex 32`; shared across API and worker |
| `DATABASE_URL` | Railway auto-injects from Postgres service | Railway provides `${{Postgres.DATABASE_URL}}` variable reference |
| `REDIS_URL` | Railway auto-injects from Redis service | Railway provides `${{Redis.REDIS_URL}}` variable reference |
| `APP_ENV` | Set to `production` | Enables JSON logging, disables debug |

**Never commit `.env` to git.** Railway injects environment variables at runtime; the app reads them via `pydantic-settings` as it already does.

**Confidence:** HIGH — This is standard practice, platform-agnostic.

---

### Frontend Serving Strategy

Two options; Option 1 is recommended for v1.1 simplicity.

**Option 1: FastAPI serves the React build (Recommended)**

Mount the Vite `dist/` directory as static files in FastAPI. One service, one URL, no CORS issues.

```python
# In main.py — add after router registration
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
```

The multi-stage Dockerfile copies `frontend/dist` → `static/` in the backend image. React Router uses HTML5 history API — ensure the mount serves `index.html` for all unmatched routes (the `html=True` flag handles this).

**Confidence:** HIGH — FastAPI static file serving is well-documented and production-ready.

**Option 2: Separate static hosting (Cloudflare Pages, Vercel)**

Build React separately, deploy to Cloudflare Pages (free). Backend runs on Railway. Requires CORS headers on FastAPI (already configured). Adds a second deploy step and potential CORS debugging.

**Not recommended for v1.1.** Extra surface area for debugging. Use Option 1.

---

### Database: PostgreSQL + pgvector

The app already uses PostgreSQL 16 + pgvector locally. In production on Railway:

1. Provision Railway Postgres service (auto-provisioned in project)
2. Railway injects `DATABASE_URL` and `LANGGRAPH_CHECKPOINT_URL` (or configure both to use the same Railway Postgres URL with different drivers)
3. Enable pgvector: connect to Railway Postgres console → `CREATE EXTENSION IF NOT EXISTS vector;`
4. Run Alembic migrations on first deploy: `alembic upgrade head` (run as a Railway deploy command or one-off)

**Driver note:** The app uses two Postgres drivers:
- `asyncpg` for FastAPI routes (`DATABASE_URL`)
- `psycopg[binary]` for LangGraph checkpointer (`LANGGRAPH_CHECKPOINT_URL`)

Both can point to the same Railway Postgres instance with different URL schemes:
- `DATABASE_URL`: `postgresql+asyncpg://user:pass@host/db`
- `LANGGRAPH_CHECKPOINT_URL`: `postgresql://user:pass@host/db` (psycopg3 sync format)

Railway provides one connection string; adjust scheme prefix per-driver.

**Confidence:** HIGH for driver configuration (stable API). MEDIUM for Railway pgvector availability (verify current support).

---

### Redis: Celery Broker and Rate Limiter

Railway managed Redis is the path of least resistance. No configuration needed beyond connecting services.

Set environment variables:
- `REDIS_URL` → from Railway Redis service
- `CELERY_BROKER_URL` → same as `REDIS_URL`
- `CELERY_RESULT_BACKEND` → same as `REDIS_URL`

The app's rate limiter (`slowapi`) already falls back to in-memory if Redis is unavailable — but with Redis on Railway, it will use the real Redis-backed limiter.

**Confidence:** HIGH — Celery + Redis on Railway is a standard pattern.

---

### Celery Worker Deployment

On Railway, deploy the Celery worker as a **separate service** in the same project, using the same Docker image but a different start command.

| Service | Start Command |
|---------|--------------|
| API service | `uvicorn main:app --host 0.0.0.0 --port 8000` |
| Celery worker | `celery -A workers.celery_app worker --loglevel=info --concurrency=1` |

Both services share the same environment variables and connect to the same Postgres and Redis instances.

`--concurrency=1` is correct for 2-5 users on a 512 MB RAM instance. LangGraph pipeline is memory-intensive (Claude responses + NetworkX graphs); more than one concurrent pipeline job per worker will OOM.

**Confidence:** HIGH — This is the standard Railway multi-service pattern.

---

### Production Hardening (Minimal, Appropriate for Scale)

These changes are needed but lightweight — not over-engineering.

| Change | Why | How |
|--------|-----|-----|
| `APP_ENV=production` env var | Switches structlog to JSON output, disables debug routes | Already in `core/settings.py` |
| CORS: restrict to known frontend origin | Currently may allow all origins in dev | Set `CORS_ORIGINS` to Railway subdomain URL in settings |
| `requirements.prod.txt` | Exclude `pyobjc-*`, `streamlit` from production image | `pip install -r requirements.prod.txt` in Dockerfile |
| Alembic migration on deploy | Ensure schema matches code | Railway "Deploy Command" or startup script: `alembic upgrade head && uvicorn ...` |
| Health check endpoint | Railway uses it to know service is ready | Already exists at `GET /health`; configure in Railway service settings |
| Non-root Docker user | Security baseline | `RUN adduser --disabled-password appuser && USER appuser` in Dockerfile |

**What NOT to add for 2-5 users:**
- Nginx reverse proxy (FastAPI + uvicorn is fine; Railway handles TLS termination)
- Kubernetes / Helm (massive overkill)
- CloudFront / CDN (static assets are tiny)
- Prometheus / Grafana monitoring (structlog JSON + Railway logs tab is enough)
- Database connection pooling (PgBouncer) — 2-5 users generates negligible connections
- Horizontal scaling / auto-scaling — single instance handles this load comfortably

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Hosting | Railway | Render | Free tier sleeps workers; Redis expires after 30 days |
| Hosting | Railway | Fly.io | Higher ops complexity; free tier uncertain post-2024 changes |
| Hosting | Railway | Heroku | No free tier since 2022; more expensive than Railway for same features |
| Hosting | Railway | DigitalOcean App Platform | $5/month minimum per service, no free tier; pgvector requires managed Postgres add-on |
| Frontend | FastAPI static mount | Separate Cloudflare Pages / Vercel | Extra deploy step, CORS debugging surface, unnecessary complexity |
| Secrets | Platform env vars | AWS SSM / HashiCorp Vault | Massive overkill for 2-5 users; adds infra to manage |
| Container registry | GHCR | DockerHub | DockerHub rate-limits unauthenticated pulls; GHCR is free with GitHub |
| OCR in production | Stub ingestion | Port pyobjc to Tesseract | Tesseract migration is correct long-term but out of scope for v1.1 deploy milestone |

---

## New Files to Add

These files do not exist in the current codebase and are needed for deployment.

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage build: Node (frontend) + Python (backend) |
| `.dockerignore` | Exclude `.venv`, `node_modules`, `*.pyc`, `.env`, test fixtures |
| `requirements.prod.txt` | Production dependencies — `requirements.txt` minus `pyobjc-*`, `streamlit` |
| `railway.toml` | Railway project config: service names, start commands, health check path |

No new Python packages are needed. No new npm packages are needed. The existing `docker-compose.yml` remains for local development and does not need to change.

---

## Cost Estimate

| Platform | Monthly Cost | What's Included |
|----------|-------------|-----------------|
| Railway Hobby | ~$5–10/month | API service + Celery worker + Postgres + Redis; $5 credit included first month |
| Pinecone (existing) | Free tier (1 index, 100K vectors) | Likely sufficient for 2-5 users' cookbook ingestion |
| Anthropic API | Pay-per-use; ~$0.50–2.00/session at Claude Sonnet pricing | Varies with use |

Total: ~$5–12/month. Well within "free or near-free" budget for a friend trial.

**Confidence:** LOW for cost estimates — API pricing and Railway plan costs change frequently. Verify current rates before budgeting.

---

## Installation / Dockerfile Commands

```bash
# Build production image locally (test before pushing)
docker build --platform linux/amd64 -t grasp:latest .

# Run locally against prod-like environment
docker run --env-file .env.prod -p 8000:8000 grasp:latest

# Deploy to Railway (after railway login and project setup)
railway up

# Or: push to GitHub main branch and Railway auto-deploys
git push origin main
```

---

## Sources

- Railway documentation (verified against training data, mid-2024): https://docs.railway.com
- Render documentation (verified against training data, early-2025): https://docs.render.com/free
- Fly.io pricing changes (training data, 2024): https://fly.io/docs/about/pricing/
- FastAPI static files documentation: https://fastapi.tiangolo.com/tutorial/static-files/
- Docker multi-stage builds: https://docs.docker.com/build/building/multi-stage/
- LangGraph PostgreSQL checkpointer: https://langchain-ai.github.io/langgraph/reference/checkpoints/
- pgvector Railway community reports: https://community.railway.com (community posts, MEDIUM confidence)

**Verification required before deploying:**
1. Confirm Railway Postgres supports `CREATE EXTENSION vector` on current Hobby plan
2. Confirm Railway Hobby plan RAM/storage limits match current pricing page
3. Confirm Render free Redis is still 30-day trial (may have changed)
4. Confirm Fly.io free allowances for new accounts

---

*Research date: 2026-03-19. Web access unavailable; based on training data through August 2025. Platform pricing and free-tier limits must be verified before platform selection.*
