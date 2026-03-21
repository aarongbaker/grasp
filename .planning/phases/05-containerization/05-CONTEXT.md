# Phase 5: Containerization - Context

**Gathered:** 2026-03-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Refine the existing Dockerfile so it reliably builds on linux/amd64, runs both the API server and Celery worker from the same image (different start commands), and ensure `.env.example` documents every required environment variable. No cloud infrastructure — local verification only.

Requirements: CONT-01, CONT-04, INFRA-03

</domain>

<decisions>
## Implementation Decisions

### Dockerfile refinements (CONT-01, CONT-04)
- Keep the existing multi-stage Dockerfile from Phase 4 as the base — it already has builder + runtime stages, Tesseract, and libpq5
- Do NOT add HEALTHCHECK, non-root USER, or ENV for secrets — those are unnecessary for the v1.1 scope (2-5 friends)
- Verify the image builds cleanly from a fresh checkout on linux/amd64
- Verify the same image can start as both API (`uvicorn main:app`) and Celery worker (`celery -A celery_app worker --concurrency=1 --pool=solo`)
- The Celery start command is already documented as a comment in the Dockerfile — ensure it actually works

### .env.example completeness (INFRA-03)
- Audit `.env.example` against the 6 required secrets from REQUIREMENTS.md: JWT_SECRET_KEY, DATABASE_URL, ANTHROPIC_API_KEY, OPENAI_API_KEY, PINECONE_API_KEY, REDIS_URL
- Add `CORS_ALLOWED_ORIGINS` with a production example value
- Add `APP_ENV` with note that production triggers strict validation
- Remove or annotate test-only variables (TEST_DATABASE_URL, TEST_LANGGRAPH_CHECKPOINT_URL) so deployers know to skip them
- Keep CELERY_BROKER_URL and CELERY_RESULT_BACKEND documented (derived from REDIS_URL but separately configurable)

### Local verification approach
- Use `docker build --platform linux/amd64` to verify cross-platform build
- Use `docker run` with CMD override to verify both start commands
- No docker-compose — manual docker run is sufficient for this scope
- Verification is the acceptance gate: if it builds and both commands start, CONT-01 and CONT-04 are satisfied

### Claude's Discretion
- Whether to consolidate LANGGRAPH_CHECKPOINT_URL with DATABASE_URL or keep separate
- Exact ordering and grouping of env vars in .env.example
- Whether to add inline comments explaining each variable's purpose

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Dockerfile & build
- `Dockerfile` — Existing multi-stage build from Phase 4 (builder + runtime with Tesseract)
- `.dockerignore` — Build context exclusions configured in Phase 4
- `requirements-prod.txt` — Production Python dependencies (no macOS packages)

### Application entry points
- `main.py` — API entry point, lifespan startup (migrations, JWT/CORS validation)
- `celery_app.py` — Celery worker entry point (verify this file exists and is correct)

### Environment configuration
- `.env.example` — Current env template (needs audit against REQUIREMENTS.md)
- `core/settings.py` — Pydantic settings that consume env vars
- `.planning/REQUIREMENTS.md` — INFRA-03 defines the 6 required secrets

### Prior phase context
- `.planning/phases/04-security-pre-deploy-hardening/04-CONTEXT.md` — Phase 4 decisions on Dockerfile, OCR, requirements split

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Dockerfile` — Already has working multi-stage build with Tesseract OCR, just needs verification
- `.env.example` — Already has 15+ variables documented, needs gap analysis against REQUIREMENTS.md
- `requirements-prod.txt` — Complete production manifest, already tested in Phase 4

### Established Patterns
- Startup validation: `main.py` lifespan raises RuntimeError for unsafe production config (JWT, CORS)
- Settings via pydantic-settings: All env vars consumed through `core/settings.py`

### Integration Points
- `celery_app.py` — Must be importable and start a worker from the Docker image
- `alembic.ini` + `alembic/` — Migrations run at API startup, must work inside container
- `core/settings.py` — All env vars flow through here; .env.example must match what Settings expects

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-containerization*
*Context gathered: 2026-03-21*
