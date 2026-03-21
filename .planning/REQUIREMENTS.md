# Requirements: GRASP v1.1

**Defined:** 2026-03-19
**Core Value:** The cook can see at a glance what to do and when — every step visible, accurately timed, in one unified view.

## v1.1 Requirements

Requirements for production deployment. Each maps to roadmap phases.

### Security

- [x] **SEC-01**: X-User-ID auth bypass is removed — all endpoints require valid JWT
- [x] **SEC-02**: API rejects startup if JWT secret is the default value when APP_ENV=production
- [x] **SEC-03**: CORS allows only the Cloudflare Pages domain in production

### Containerization

- [x] **CONT-01**: Single multi-stage Dockerfile builds API and worker from one image
- [x] **CONT-02**: Production requirements file excludes macOS-only packages (pyobjc-*)
- [x] **CONT-03**: `.dockerignore` excludes node_modules, .venv, .git, .planning
- [x] **CONT-04**: Image builds and runs successfully on linux/amd64

### Infrastructure

- [ ] **INFRA-01**: Railway project provisioned with Postgres (pgvector enabled) and Redis
- [ ] **INFRA-02**: All secrets (JWT_SECRET_KEY, DATABASE_URL, ANTHROPIC_API_KEY, OPENAI_API_KEY, PINECONE_API_KEY, REDIS_URL) configured as Railway env vars
- [x] **INFRA-03**: `.env.example` documents all required environment variables

### Backend

- [ ] **BACK-01**: API service runs on Railway and responds to health checks
- [ ] **BACK-02**: Celery worker runs as separate Railway service with concurrency=1, --pool=solo
- [ ] **BACK-03**: Alembic migrations run on startup before API accepts traffic
- [ ] **BACK-04**: Full pipeline (generate → enrich → validate → schedule → render) works end-to-end on Railway

### Frontend

- [ ] **FRONT-01**: React SPA deployed to Cloudflare Pages
- [ ] **FRONT-02**: VITE_API_URL environment variable configures API base URL at build time
- [ ] **FRONT-03**: SPA routing works (all paths serve index.html)

### OCR

- [x] **OCR-01**: PDF ingestion uses Tesseract on Linux instead of macOS Vision framework
- [x] **OCR-02**: Tesseract installed in Docker image with required language packs

## Future Requirements

Deferred to v1.2+.

- **DEPLOY-01**: Custom domain with HTTPS
- **DEPLOY-02**: CI/CD pipeline (auto-deploy on push)
- **DEPLOY-03**: Monitoring and alerting (uptime, error rates)
- **DEPLOY-04**: Auto-scaling for higher user counts
- **DEPLOY-05**: Invite code gate for controlled access

## Out of Scope

| Feature | Reason |
|---------|--------|
| CI/CD pipeline | Manual deploy sufficient for 2-5 users |
| Custom domain | Platform subdomain is fine for friend group |
| Monitoring/alerting | Not needed at this scale |
| Auto-scaling | Single instance sufficient |
| Drag-to-reschedule UI | Visualization only, not a scheduling editor |

## Traceability

Which phases cover which requirements.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SEC-01 | Phase 4 | Complete |
| SEC-02 | Phase 4 | Complete |
| SEC-03 | Phase 4 | Complete |
| CONT-02 | Phase 4 | Complete |
| CONT-03 | Phase 4 | Complete |
| OCR-01 | Phase 4 | Complete |
| OCR-02 | Phase 4 | Complete |
| CONT-01 | Phase 5 | Complete |
| CONT-04 | Phase 5 | Complete |
| INFRA-03 | Phase 5 | Complete |
| INFRA-01 | Phase 6 | Pending |
| INFRA-02 | Phase 6 | Pending |
| BACK-01 | Phase 7 | Pending |
| BACK-02 | Phase 7 | Pending |
| BACK-03 | Phase 7 | Pending |
| BACK-04 | Phase 7 | Pending |
| FRONT-01 | Phase 7 | Pending |
| FRONT-02 | Phase 7 | Pending |
| FRONT-03 | Phase 7 | Pending |

**Coverage:**
- v1.1 requirements: 19 total
- Mapped to phases: 19
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-19*
*Last updated: 2026-03-19 — traceability populated by roadmapper*
