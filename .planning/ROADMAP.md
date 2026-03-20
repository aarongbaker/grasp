# Roadmap: GRASP — Deploy to Production

## Milestones

- ✅ **v1.0 Schedule UI & Pipeline Fixes** — Phases 1-3 (shipped 2026-03-20)
- 🚧 **v1.1 Deploy to Production** — Phases 4-7 (in progress)

## Phases

<details>
<summary>✅ v1.0 Schedule UI & Pipeline Fixes (Phases 1-3) — SHIPPED 2026-03-20</summary>

- [x] Phase 1: Gantt Fix (2/2 plans) — completed 2026-03-19
- [x] Phase 2: Prep-Ahead Fix (1/1 plan) — completed 2026-03-20
- [x] Phase 3: Unified Timeline (1/1 plan) — completed 2026-03-20

Full details: [milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md)

</details>

### 🚧 v1.1 Deploy to Production (In Progress)

**Milestone Goal:** GRASP running on a public URL so 2-5 friends can try it out.

- [ ] **Phase 4: Security & Pre-Deploy Hardening** — Remove auth bypass, harden JWT guard, add CORS config, fix Linux OCR compatibility
- [ ] **Phase 5: Containerization** — Multi-stage Dockerfile, production requirements, env documentation
- [ ] **Phase 6: Infrastructure Provisioning** — Railway project, Postgres+pgvector, Redis, all secrets configured
- [ ] **Phase 7: Deploy & End-to-End Validation** — Backend and frontend live on public URLs, full pipeline verified

## Phase Details

### Phase 4: Security & Pre-Deploy Hardening
**Goal**: The codebase is safe to deploy — auth bypass removed, secrets enforced, and Linux-buildable
**Depends on**: Phase 3
**Requirements**: SEC-01, SEC-02, SEC-03, CONT-02, CONT-03, OCR-01, OCR-02
**Success Criteria** (what must be TRUE):
  1. Any API request without a valid JWT token receives 401 — no X-User-ID header bypass possible
  2. API startup fails with a clear error when JWT_SECRET_KEY is the default value and APP_ENV=production
  3. API rejects cross-origin requests from any domain other than the configured Cloudflare Pages domain
  4. Docker image builds successfully on linux/amd64 without pyobjc errors
  5. `.dockerignore` excludes node_modules, .venv, .git, and .planning from the image build context
**Plans**: TBD

### Phase 5: Containerization
**Goal**: A single Docker image builds and runs both the API and Celery worker, verifiable locally before touching the cloud
**Depends on**: Phase 4
**Requirements**: CONT-01, CONT-04, INFRA-03
**Success Criteria** (what must be TRUE):
  1. `docker build` succeeds on linux/amd64 from a clean checkout using the multi-stage Dockerfile
  2. The same image starts as the API (`uvicorn`) and as the Celery worker (different start command) without modification
  3. `.env.example` documents every required environment variable so any new developer can configure the app from scratch
**Plans**: TBD

### Phase 6: Infrastructure Provisioning
**Goal**: All cloud services exist and are configured — Railway project running with Postgres (pgvector enabled), Redis, and all secrets loaded
**Depends on**: Phase 5
**Requirements**: INFRA-01, INFRA-02
**Success Criteria** (what must be TRUE):
  1. Railway project has Postgres plugin with pgvector extension enabled (`CREATE EXTENSION vector` succeeds)
  2. Railway project has Redis plugin accessible to both the API and Celery worker services
  3. All six required secrets (JWT_SECRET_KEY, DATABASE_URL, ANTHROPIC_API_KEY, OPENAI_API_KEY, PINECONE_API_KEY, REDIS_URL) are set as Railway environment variables
**Plans**: TBD

### Phase 7: Deploy & End-to-End Validation
**Goal**: GRASP is live on public URLs — backend and frontend deployed, full pipeline working, friends can register and use the app
**Depends on**: Phase 6
**Requirements**: BACK-01, BACK-02, BACK-03, BACK-04, FRONT-01, FRONT-02, FRONT-03
**Success Criteria** (what must be TRUE):
  1. `GET /api/v1/health` on the Railway URL returns 200 and the Celery worker is processing tasks
  2. Alembic migrations run automatically at API startup — the database schema is current before the first request is served
  3. A user can register, log in, create a session, run the full generate→enrich→validate→schedule→render pipeline, and view the Gantt result on the public URL
  4. React SPA is served from a Cloudflare Pages URL and all deep-link routes (e.g., `/session/123`) work on hard refresh
  5. The frontend calls the Railway API using an absolute URL configured via VITE_API_URL — no hardcoded localhost references
**Plans**: TBD

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Gantt Fix | v1.0 | 2/2 | Complete | 2026-03-19 |
| 2. Prep-Ahead Fix | v1.0 | 1/1 | Complete | 2026-03-20 |
| 3. Unified Timeline | v1.0 | 1/1 | Complete | 2026-03-20 |
| 4. Security & Pre-Deploy Hardening | v1.1 | 0/? | Not started | - |
| 5. Containerization | v1.1 | 0/? | Not started | - |
| 6. Infrastructure Provisioning | v1.1 | 0/? | Not started | - |
| 7. Deploy & End-to-End Validation | v1.1 | 0/? | Not started | - |
