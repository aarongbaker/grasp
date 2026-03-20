# Phase 4: Security & Pre-Deploy Hardening - Context

**Gathered:** 2026-03-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Make the codebase safe to deploy: remove the X-User-ID auth bypass so all endpoints require JWT, enforce secret validation and CORS lockdown at startup in production, create `.dockerignore`, and add Tesseract OCR as the Linux backend for cookbook ingestion. No new features — hardening only.

</domain>

<decisions>
## Implementation Decisions

### Auth bypass removal (SEC-01)
- Remove the entire X-User-ID fallback path from `core/auth.py` (lines 70-75)
- 401 error message becomes simple: "Missing or invalid authentication token." — no implementation details leaked
- Public routes (no JWT required): `/health`, `/register`, `/login`, `/refresh` only
- All other routes require valid JWT — no exceptions
- Tests migrate to a JWT test helper that generates valid tokens for test users (mirrors production auth exactly)
- No dependency override pattern — tests exercise the real JWT path

### JWT secret validation (SEC-02)
- Already implemented in `main.py` lines 51-59 — startup raises RuntimeError if JWT_SECRET_KEY is default and APP_ENV=production
- Verify this works correctly; no design changes needed

### CORS configuration (SEC-03)
- CORS origins read from `CORS_ALLOWED_ORIGINS` env var (comma-separated), already wired via `cors_allowed_origins` in settings.py
- Development: keep current permissive defaults (localhost:3000, localhost:8501) when APP_ENV != production
- Production: API fails to start if CORS origins are still the dev defaults — same fail-loud pattern as JWT secret check
- No hardcoded production domain — Cloudflare Pages URL set via env var at deploy time

### OCR / Tesseract integration (OCR-01, OCR-02)
- Implement Tesseract as the Linux OCR backend in `ingestion/rasteriser.py`
- Platform-aware: Apple Vision on macOS (better quality for cookbook fonts), Tesseract on Linux — runtime detection via `sys.platform`
- English language pack only (`tesseract-ocr` + `tesseract-ocr-eng`) — keeps Docker image smaller
- Tesseract installed in Docker image via apt-get
- Existing pymupdf fallback remains as last resort if both Vision and Tesseract fail

### Production requirements (CONT-02)
- pyobjc excluded from production/Docker requirements — Claude's discretion on the cleanest split approach (separate requirements file vs. Dockerfile filtering)

### Dockerignore (CONT-03)
- Required exclusions: `node_modules/`, `.venv/`, `.git/`, `.planning/`
- Additional exclusions: `.env*`, `tests/`, `streamlit_app.py`, `__pycache__/`, `frontend/`, `docs/`, `README.md`, `CLAUDE.md`
- Frontend excluded because it deploys independently to Cloudflare Pages

### Claude's Discretion
- pyobjc requirements split strategy (separate file vs. Dockerfile filtering)
- Tesseract integration details in rasteriser.py (pytesseract wrapper vs. subprocess)
- Exact JWT test helper implementation
- Whether to add a startup check that Tesseract binary exists when running on Linux

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Auth & security
- `core/auth.py` — Current auth implementation with X-User-ID bypass to remove
- `core/settings.py` — JWT and CORS settings, `jwt_secret_is_default` property
- `core/deps.py` — CurrentUser dependency injection pattern
- `main.py` — Startup validation (JWT secret check), CORS middleware setup

### OCR / ingestion
- `ingestion/rasteriser.py` — Current Apple Vision OCR with pymupdf fallback, needs Tesseract path added

### Requirements
- `.planning/REQUIREMENTS.md` — SEC-01, SEC-02, SEC-03, CONT-02, CONT-03, OCR-01, OCR-02 definitions

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `core/settings.py:Settings` — Already has `cors_allowed_origins`, `jwt_secret_is_default`, `app_env` — CORS lockdown builds on these
- `main.py` lifespan — JWT secret validation pattern (lines 51-59) can be replicated for CORS check
- `ingestion/rasteriser.py` — Conditional import pattern (lines 104, 120-125) already handles platform-aware OCR selection

### Established Patterns
- Startup validation: raise RuntimeError in lifespan if config is unsafe for production
- Auth dependency injection: all routes use `CurrentUser` from `core/deps.py`, so auth changes are centralized
- Platform detection: `sys.platform == "darwin"` already used in rasteriser.py

### Integration Points
- `core/auth.py:get_current_user()` — Single function to modify for auth bypass removal
- `main.py` lifespan — Add CORS validation alongside existing JWT check
- `tests/` — All API tests using X-User-ID headers need migration to JWT helper

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

*Phase: 04-security-pre-deploy-hardening*
*Context gathered: 2026-03-19*
