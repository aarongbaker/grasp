# Project Research Summary

**Project:** GRASP v1.1 — Production Deployment
**Domain:** Multi-service Python/React app deployment to free/low-cost hosting
**Researched:** 2026-03-19
**Confidence:** HIGH (codebase-verified; platform pricing requires live verification)

## Executive Summary

GRASP is a fully-functional meal planning application that exists only on a developer's laptop. The v1.1 milestone is purely an infrastructure milestone: take a working local app (FastAPI + Celery + PostgreSQL/pgvector + Redis + React SPA) and make it accessible to 2-5 friends via a public URL. Research confirms the core pipeline is complete and the deployment work is bounded and well-understood. The recommended approach is Railway.app for the backend services (API + Celery worker + managed Postgres + managed Redis in one project) and Cloudflare Pages for the React frontend. Total infrastructure cost: ~$5–10/month. Estimated implementation time: 4–8 hours of actual work.

There are two hard blockers that must be resolved before containerization can begin. First, `requirements.txt` contains macOS-only `pyobjc-*` packages that prevent a Linux Docker build — the fix is a `requirements.prod.txt` that excludes them, disabling cookbook ingestion for v1.1. Second, `core/auth.py` contains a legacy `X-User-ID` authentication bypass that allows any user to impersonate any other user by knowing their UUID. This is a two-line deletion that must happen before the URL is shared. Beyond these blockers, the deployment path is linear and all major patterns are well-documented.

The highest-confidence section of research is the pitfalls analysis, which is entirely code-verified. Every risk identified (JWT default secret, CORS JSON parsing, Celery concurrency OOM, Vite relative URL, LangGraph/Alembic table conflict) has a confirmed root cause in the existing codebase and a clear, specific fix. None of the critical pitfalls require architectural changes — they are all configuration or small code changes. The platform choice (Railway vs. Render vs. Fly.io) is the only area of genuine uncertainty, driven by pricing pages that change frequently.

## Key Findings

### Recommended Stack

The existing application stack is locked and correct. No new application dependencies are needed. The deployment additions are purely infrastructure: a `Dockerfile`, a `requirements.prod.txt`, a `railway.toml`, and a `.env.example`. The single most important architectural decision is how to serve the React frontend. Research recommends **Cloudflare Pages** (free, CDN-backed, independent deploy cycle) over serving static files from FastAPI (wastes Railway compute, couples frontend and backend deploys). This requires adding a `VITE_API_URL` environment variable to the frontend config.

**Core technologies:**
- **Docker** (multi-stage): One image used by both Railway API and worker services — prevents code drift between services
- **Railway.app**: Primary hosting platform — pgvector support confirmed, Redis + Postgres as first-class plugins, no sleep on inactivity, ~$5–10/month for all four services
- **Cloudflare Pages**: Frontend hosting — free, global CDN, zero compute cost, independent deploy from backend
- **GitHub Container Registry (GHCR)**: Container registry — free with GitHub account, avoids DockerHub rate limits
- **`requirements.prod.txt`**: Production dependency file excluding `pyobjc-*` and `streamlit` — required to build on Linux

**Critical platform caveat:** Railway's pgvector availability and Hobby plan limits must be verified against current pricing before committing. Training data is August 2025; platform pricing changes frequently.

### Expected Features

Research confirmed exactly what exists and what is missing by direct codebase inspection. There is no feature ambiguity — the gaps are infrastructure gaps, not capability gaps.

**Must have (table stakes — currently missing):**
- Backend Dockerfile — app cannot run anywhere without it (1–2h)
- Production `docker-compose.prod.yml` — current compose is dev-only with hardcoded `grasp:grasp` credentials (1–2h)
- pgvector Postgres image (`pgvector/pgvector:pg16`) — current compose uses `postgres:16-alpine` which lacks the extension (15m)
- `.env.example` — required secrets are undocumented; friends cannot configure what they cannot see (30m)
- CORS production origin — defaults to `localhost:3000`; every API call from the deployed frontend will fail (15m)
- SPA 404 fallback — React Router deep links break on hard refresh without a catch-all route (1–2h)
- `VITE_API_URL` env var — frontend uses a hardcoded relative `/api/v1` path, which 404s when frontend and backend are on different origins

**Should have (improve beta experience):**
- Invite code / registration gate — prevents strangers from using paid API keys if URL becomes public
- Extended health check (Redis ping) — current `/health` only pings DB; broken Celery worker fails silently
- Admin seed script — creates first user without needing a browser

**Defer to v2+:**
- Email verification (requires email provider; zero value for 5 known friends)
- Password reset flow (same reason)
- CI/CD pipeline (out of scope per PROJECT.md)
- Custom domain (platform subdomain is fine for beta)
- Database backups (data is ephemeral in beta)

### Architecture Approach

The production architecture adds two external services to the existing topology: Railway provides managed Postgres and Redis replacing the local Docker containers, while Cloudflare Pages hosts the React SPA build. The FastAPI and Celery worker run as two separate Railway services built from one Docker image, distinguished only by their start command. This one-image-two-commands pattern is the critical architectural decision — it prevents code drift between the API and worker, which share most of the codebase.

**Major components:**
1. **Railway API service** (`uvicorn main:app --host 0.0.0.0 --port $PORT`) — handles auth, session CRUD, pipeline enqueue, status polling; runs Alembic migrations at startup
2. **Railway Worker service** (`celery -A workers.celery_app worker --pool=solo --concurrency=1`) — executes LangGraph pipeline; creates its own graph + checkpointer per task (already correct in codebase)
3. **Cloudflare Pages** — serves React SPA build output; calls Railway API via absolute URL set at build time via `VITE_API_URL`
4. **Railway Postgres plugin** — PostgreSQL 16 + pgvector; pgvector extension requires one manual `CREATE EXTENSION vector;` before first deploy
5. **Railway Redis plugin** — Celery broker (db 0) + result backend (db 1) + slowapi rate limiting

**Key pattern: database driver split.** The API uses `asyncpg` (`postgresql+asyncpg://...`), while LangGraph's PostgresSaver uses `psycopg3` (`postgresql://...`). Both point to the same Railway Postgres instance but with different URL scheme prefixes. These must be configured as separate env vars (`DATABASE_URL` and `LANGGRAPH_CHECKPOINT_URL`).

### Critical Pitfalls

1. **X-User-ID authentication bypass active in production** — `core/auth.py` accepts `X-User-ID: <uuid>` header, bypassing JWT entirely. Delete the `elif x_user_id:` block before sharing the URL. Any user who receives an API response containing another user's UUID can authenticate as them.

2. **Celery concurrency of 4 causes OOM kill on free-tier hosts** — `core/settings.py` defaults `celery_worker_concurrency = 4`. Each concurrent pipeline run uses 150–300 MB (LangChain + asyncpg + NetworkX + Claude response buffers). At concurrency 4: 600 MB–1.2 GB, far exceeding Railway's 512 MB service limit. Set `CELERY_WORKER_CONCURRENCY=1` and use `--pool=solo` in the worker start command.

3. **CORS origins are malformed JSON causing startup crash** — `pydantic-settings` parses list fields from env vars as JSON. Setting `CORS_ALLOWED_ORIGINS=https://grasp.pages.dev` (without JSON array syntax) causes a Pydantic validation error at startup. Correct format: `CORS_ALLOWED_ORIGINS='["https://grasp.pages.dev"]'` — outer single quotes, inner double quotes.

4. **pyobjc packages block Linux Docker build** — `requirements.txt` includes macOS-only Vision framework packages. Even with `sys_platform == "darwin"` markers (which prevents install), the OCR code path calls Apple Vision APIs at runtime on Linux, causing the ingestion task to fail. Resolution: create `requirements.prod.txt` excluding `pyobjc-*`, and disable or stub the cookbook ingestion endpoint for v1.1.

5. **LangGraph checkpoint tables conflict with Alembic autogenerate** — `checkpointer.setup()` creates `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` tables outside Alembic's control. Running `alembic revision --autogenerate` against production will generate DROP statements for these tables. Add an `include_object` filter in `alembic/env.py` to exclude tables starting with "checkpoint" before any migration runs against the production database.

## Implications for Roadmap

Based on combined research, the deployment work naturally divides into five sequential phases with hard dependencies between them.

### Phase 1: Security Hardening and Pre-Deploy Fixes
**Rationale:** Two security issues (X-User-ID bypass, JWT secret guard) and one build blocker (pyobjc Linux incompatibility) must be resolved before any other work begins. These are code changes to the existing application, not infrastructure. Doing them first means every subsequent phase builds on a secure, Linux-buildable codebase.
**Delivers:** A codebase that is safe to deploy and can be containerized on Linux
**Addresses:** Auth bypass (Pitfall 13), JWT secret guard hardening (Pitfall 1), macOS OCR blocker (Pitfall 11)
**Changes:** Delete `elif x_user_id:` block in `auth.py`; create `requirements.prod.txt`; add Alembic `include_object` filter; harden JWT guard to fire regardless of `APP_ENV`

### Phase 2: Containerization
**Rationale:** The Dockerfile is a prerequisite for every subsequent phase — Railway, local testing, and CI all depend on it. This phase is entirely local work with no external dependencies. It can be tested and validated before any cloud account is touched.
**Delivers:** A working Docker image that runs the FastAPI API and can be started with a different command for Celery
**Uses:** Multi-stage Dockerfile (Node build → Python backend), `requirements.prod.txt`, `.dockerignore`, `.env.example`
**Avoids:** Image size bloat, `pyobjc` Linux install failures, accidental secret commits

### Phase 3: Infrastructure Provisioning
**Rationale:** Platform must exist before services can be deployed to it. This phase is pure cloud console work — no code changes.
**Delivers:** Railway project with Postgres + Redis plugins; pgvector extension enabled; env vars configured in Railway dashboard; Cloudflare Pages project connected to GitHub
**Addresses:** pgvector extension requirement (Pitfall 2), CORS origin chicken-and-egg (deploy first to get the URL, then set CORS)
**Verification needed:** Confirm Railway Postgres supports `CREATE EXTENSION vector` on current Hobby plan before committing to platform

### Phase 4: Backend Service Deploy
**Rationale:** API must be deployed and validated before the frontend can be configured to call it. The API URL must be known to configure Cloudflare Pages.
**Delivers:** Public Railway API URL; health check passing; Alembic migrations confirmed; Celery worker processing test task
**Addresses:** CORS JSON format (Pitfall 4), Celery concurrency OOM (Pitfall 5), Alembic localhost fallback (Pitfall 15), database URL driver split
**Key step:** Set `CELERY_WORKER_CONCURRENCY=1`, `--pool=solo` on worker start command; verify `/api/v1/health` returns 200; run smoke test against `POST /api/v1/users`

### Phase 5: Frontend Deploy and End-to-End Validation
**Rationale:** Frontend deploy is last because it requires the API URL (from Phase 4) and the Cloudflare Pages URL (needed to set CORS on the API). This phase closes the loop and produces a working public URL.
**Delivers:** Public Cloudflare Pages URL; complete end-to-end flow (register → create session → run pipeline → view results)
**Addresses:** Vite relative URL (Pitfall 10), CORS final configuration, SPA 404 fallback
**Key step:** Set `VITE_API_URL` in Cloudflare Pages env vars; update CORS on Railway API to include Pages URL; redeploy API; run full user flow test

### Phase Ordering Rationale

- Phase 1 before everything: security issues are non-negotiable and pyobjc blocks containerization
- Phase 2 before cloud: local Docker validation catches issues before they become harder-to-debug cloud failures
- Phase 3 before Phase 4: infrastructure must exist before deployment; pgvector must be enabled before first Alembic migration
- Phase 4 before Phase 5: frontend needs the API URL; CORS cannot be finalized until both URLs are known (classic chicken-and-egg — workaround: use wildcard CORS temporarily during initial frontend deploy)
- Phase 5 includes end-to-end validation: only after both services are live can the full user flow be confirmed

### Research Flags

Phases with well-documented patterns (skip `/gsd:research-phase`):
- **Phase 1 (Security hardening):** All changes are specific code-verified fixes with exact line numbers. No research needed.
- **Phase 2 (Containerization):** Multi-stage Dockerfile for FastAPI + React is a standard, well-documented pattern. HIGH confidence.
- **Phase 5 (Frontend deploy):** Cloudflare Pages + Vite build is extremely well-documented. No research needed.

Phases that may benefit from brief research during planning:
- **Phase 3 (Infrastructure provisioning):** Railway pgvector support and current Hobby plan limits must be verified against live documentation before committing to the platform. If Railway pgvector is unavailable on Hobby tier, fall back to Supabase (free, pgvector confirmed) + Railway for Redis + Fly.io or Render for API — more complex but workable.
- **Phase 4 (Backend deploy):** The asyncpg vs. psycopg3 dual-URL configuration has some nuance; verify LangGraph checkpoint-postgres connection string format against current library docs if issues arise at deploy time.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM | Application stack is HIGH confidence (code-verified). Platform selection is MEDIUM — Railway pricing and pgvector availability must be verified against live docs before Phase 3 begins. |
| Features | HIGH | Features research is based entirely on direct codebase inspection. Every gap identified is a confirmed gap. No guesswork. |
| Architecture | HIGH | Codebase analysis of `main.py`, `workers/tasks.py`, `core/settings.py`, `docker-compose.yml` gives HIGH confidence on component boundaries and data flow. Cloudflare Pages vs. FastAPI static serving is an architectural opinion, not a fact — either works. |
| Pitfalls | HIGH | Every critical pitfall is code-verified with specific file and line references. The risks are real, confirmed, and have specific fixes. Minor pitfalls (polling interval, Redis persistence) are lower priority but also verified. |

**Overall confidence:** HIGH for what to build and in what order. MEDIUM for which specific hosting platform to use.

### Gaps to Address

- **Railway pgvector on Hobby plan:** Must verify before Phase 3. If unavailable, alternative is Supabase (free tier, pgvector confirmed working with LangGraph) for Postgres only, keeping Railway for Redis + API + worker.
- **Railway Hobby plan RAM limits:** The 512 MB figure is from training data. Verify current limit — if it has decreased, `--concurrency=1 --pool=solo` may still be insufficient for the LangGraph pipeline.
- **Ingestion feature scope for v1.1:** Research recommends disabling cookbook ingestion (stub the endpoint) for v1.1 to unblock containerization. Confirm this is acceptable before Phase 1. If ingestion must work in v1.1, allocate additional time to implement a Tesseract fallback in Phase 1.
- **Frontend API URL approach:** Research notes a tension between two valid options — FastAPI serving static files (simpler, one service) vs. Cloudflare Pages (better CDN, independent deploy). ARCHITECTURE.md recommends Cloudflare Pages; STACK.md and PITFALLS.md both suggest FastAPI static serving as the simpler option. Make this decision explicitly before Phase 2 (Dockerfile design depends on it). **Recommendation: Cloudflare Pages** — the independent deploy cycle is worth the added CORS configuration.

## Sources

### Primary (HIGH confidence — codebase-verified)
- `/main.py` — CORS, rate limiting, lifespan, Alembic migration hook, JWT guard
- `/core/settings.py` — all env vars, defaults, Celery concurrency setting
- `/core/auth.py` — X-User-ID bypass (lines confirmed active)
- `/workers/tasks.py` — Celery worker graph instantiation pattern
- `/frontend/src/api/client.ts` — relative URL (`/api/v1`), 30s timeout, refresh logic
- `/docker-compose.yml` — confirms no Dockerfile, dev-only Postgres/Redis
- `/requirements.txt` — confirms `pyobjc-*` with `sys_platform == "darwin"` markers
- `/alembic/env.py` and `/alembic.ini` — confirms localhost fallback URL, no `include_object` filter
- `/.planning/PROJECT.md` — milestone goals and explicit out-of-scope list

### Secondary (MEDIUM confidence — training data, August 2025)
- Railway documentation: https://docs.railway.com — pgvector support, Hobby plan limits
- Railway community reports on pgvector: https://community.railway.com
- Render documentation: https://docs.render.com/free — free tier sleep behavior

### Tertiary (LOW confidence — verify before use)
- Railway Hobby plan pricing: https://railway.com/pricing — verify current monthly cost and RAM limits
- Fly.io free allowances for new accounts: https://fly.io/docs/about/pricing/ — changed significantly in 2024
- Render free Redis expiry: 30-day trial as of early 2025 — may have changed

---
*Research completed: 2026-03-19*
*Ready for roadmap: yes*
