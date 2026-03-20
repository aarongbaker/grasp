---
phase: 04-security-pre-deploy-hardening
verified: 2026-03-19T00:00:00Z
status: gaps_found
score: 6/7 must-haves verified
re_verification: false
gaps:
  - truth: "Tesseract installed in Docker image with required language packs (OCR-02)"
    status: failed
    reason: "No Dockerfile exists anywhere in the codebase. OCR-02 was marked complete in REQUIREMENTS.md but the Docker image that would install tesseract-ocr has not been authored. The plan notes acknowledged this is deferred to a subsequent Dockerfile task, but the requirement is mapped to Phase 4."
    artifacts:
      - path: "Dockerfile"
        issue: "File does not exist — Tesseract system package cannot be installed in a non-existent image"
    missing:
      - "A Dockerfile (or Dockerfile.prod) that includes RUN apt-get install -y tesseract-ocr tesseract-ocr-eng"
---

# Phase 4: Security & Pre-Deploy Hardening Verification Report

**Phase Goal:** The codebase is safe to deploy — auth bypass removed, secrets enforced, and Linux-buildable
**Verified:** 2026-03-19
**Status:** gaps_found
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | Any API request without a valid JWT token receives 401 — no X-User-ID bypass possible | VERIFIED | `core/auth.py`: `x_user_id` parameter removed from `get_current_user()` signature; zero occurrences of `x_user_id` or `X-User-ID` in `core/`, `tests/`, `api/` |
| 2 | API startup fails with clear error when JWT_SECRET_KEY is default and APP_ENV=production | VERIFIED | `main.py` lines 51-59: `if settings.jwt_secret_is_default` + `if settings.app_env == "production"` raises `RuntimeError` with descriptive message |
| 3 | CORS startup validation blocks dev-default origins in production | VERIFIED | `main.py` lines 61-71: `_DEV_ORIGINS` set, subset comparison `configured_origins <= _DEV_ORIGINS` raises `RuntimeError` with `"CORS_ALLOWED_ORIGINS must be set"` |
| 4 | Production requirements file builds on Linux without pyobjc errors | VERIFIED | `requirements-prod.txt` exists; grep confirms 0 occurrences of `pyobjc`, `pytest`, `streamlit`, `httpx`; `pytesseract==0.3.10` present |
| 5 | .dockerignore excludes node_modules, .venv, .git, .planning from build context | VERIFIED | `.dockerignore` exists and contains all required exclusions: `.git/`, `.venv/`, `node_modules/`, `frontend/`, `.planning/`, `tests/`, `docs/`, `__pycache__/`, `.env*`, `streamlit_app.py` |
| 6 | Tesseract OCR is used on Linux when pytesseract is available; Apple Vision and pymupdf preserved | VERIFIED | `ingestion/rasteriser.py`: `_HAS_TESSERACT` detection flag at module level; `_ocr_page_tesseract()` function defined; three-branch selection logic (`is_mac` / `elif _HAS_TESSERACT` / `else`); import succeeds on macOS without Tesseract installed |
| 7 | Tesseract installed in Docker image with required language packs (OCR-02) | FAILED | No `Dockerfile` exists anywhere in the codebase. The rasteriser code is ready but the Docker image that would `RUN apt-get install tesseract-ocr` has not been authored. |

**Score:** 6/7 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `core/auth.py` | JWT-only auth, no X-User-ID fallback, generic 401 messages | VERIFIED | `_AUTH_ERROR = "Missing or invalid authentication token."` constant used for all 401 paths; `get_current_user` signature has only `authorization` + `db`; zero `x_user_id` or `X-User-ID` occurrences |
| `main.py` | CORS production validation in lifespan, reads `CORS_ALLOWED_ORIGINS` env var | VERIFIED | `_DEV_ORIGINS` set comparison on lines 61-71; `RuntimeError` raised for dev-default origins in production; CORS middleware uses `settings.cors_allowed_origins` |
| `tests/test_auth.py` | JWT-only auth tests, `test_jwt_bearer_auth_generic_error_message` added | VERIFIED | 6 tests total; no `x_user_id`/`X-User-ID` occurrences; `test_legacy_x_user_id_still_works` and `test_jwt_takes_priority_over_x_user_id` removed; `test_jwt_bearer_auth_generic_error_message` present |
| `tests/test_api_routes.py` | JWT-only route tests replacing X-User-ID tests | VERIFIED | `test_auth_invalid_token_returns_401` and `test_auth_valid_token_unknown_user_returns_404` present; zero `X-User-ID` occurrences; 14 tests total |
| `.dockerignore` | Docker build context exclusions | VERIFIED | All required paths excluded: `.git/`, `.venv/`, `node_modules/`, `frontend/`, `.planning/`, `tests/`, `docs/`, `CLAUDE.md`, `README.md`, `streamlit_app.py` |
| `requirements-prod.txt` | Production-only Python dependencies | VERIFIED | `pytesseract==0.3.10` present; all production packages present; zero `pyobjc`, `pytest`, `streamlit`, `httpx` |
| `ingestion/rasteriser.py` | Platform-aware OCR with three tiers | VERIFIED | `_HAS_TESSERACT` flag, `_ocr_page_tesseract()`, `_ocr_page_apple_vision()`, `_ocr_page_pymupdf_fallback()` all present; three-branch dispatch logic correct |
| `Dockerfile` | Installs `tesseract-ocr` system package in Docker image (OCR-02) | MISSING | File does not exist anywhere in the repo |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `core/auth.py` | `core/deps.py` | `get_current_user` dependency | WIRED | `CurrentUser = Annotated[UserProfile, Depends(get_current_user)]` in `core/deps.py`; function signature matches (`authorization`, `db` only) |
| `main.py` | `core/settings.py` | `settings.cors_allowed_origins` + `settings.jwt_secret_is_default` | WIRED | Both properties read in lifespan; `CORS_ALLOWED_ORIGINS` env var controls `settings.cors_allowed_origins` via Pydantic settings |
| `ingestion/rasteriser.py` | `pytesseract` | `_HAS_TESSERACT` flag + conditional import inside `_ocr_page_tesseract()` | WIRED | Module-level detection; function-level conditional import; graceful degradation when pytesseract absent |
| `requirements-prod.txt` | `requirements.txt` | subset without pyobjc and dev packages | WIRED | All production packages from `requirements.txt` present; excluded packages confirmed absent |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| SEC-01 | 04-01-PLAN.md | X-User-ID auth bypass removed — all endpoints require valid JWT | SATISFIED | `get_current_user` signature has no `x_user_id`; zero occurrences in codebase; 20/20 tests pass |
| SEC-02 | 04-01-PLAN.md | API rejects startup if JWT secret is default when APP_ENV=production | SATISFIED | `main.py` lines 51-59: `RuntimeError` raised for default secret in production |
| SEC-03 | 04-01-PLAN.md | CORS allows only configured production domain | SATISFIED | `main.py` lines 61-71: `RuntimeError` raised for dev-default CORS origins in production; `CORS_ALLOWED_ORIGINS` env var controls allowed origins |
| CONT-02 | 04-02-PLAN.md | Production requirements file excludes macOS-only packages | SATISFIED | `requirements-prod.txt` exists; zero `pyobjc` occurrences |
| CONT-03 | 04-02-PLAN.md | `.dockerignore` excludes node_modules, .venv, .git, .planning | SATISFIED | `.dockerignore` present with all required exclusions |
| OCR-01 | 04-02-PLAN.md | PDF ingestion uses Tesseract on Linux instead of macOS Vision framework | SATISFIED | `ingestion/rasteriser.py` three-branch OCR selection: `elif _HAS_TESSERACT` branch for Linux; Apple Vision preserved for macOS |
| OCR-02 | 04-02-PLAN.md | Tesseract installed in Docker image with required language packs | BLOCKED | No `Dockerfile` exists. The rasteriser code is Tesseract-capable and `requirements-prod.txt` includes `pytesseract==0.3.10`, but the system package (`tesseract-ocr`) cannot be installed without a Dockerfile. `REQUIREMENTS.md` shows this as `[x]` complete but no evidence supports that mark. |

**Note on OCR-02 in REQUIREMENTS.md:** The requirement checkbox is marked `[x]` complete and the traceability table lists it as "Phase 4 — Complete". This appears premature. Plan 04-02 itself notes in the summary: *"tesseract-ocr system package must be present in the Docker image (handled in a subsequent Dockerfile task)"* — which confirms the Dockerfile work was deferred. OCR-02 should remain open and be addressed in Phase 5 (Containerization).

---

### Anti-Patterns Found

No anti-patterns detected in any of the modified files. Scanned for TODO/FIXME/PLACEHOLDER, empty return stubs, and console.log-only implementations — none found in `core/auth.py`, `main.py`, `.dockerignore`, `requirements-prod.txt`, or `ingestion/rasteriser.py`.

---

### Human Verification Required

None required for the 6 verified truths. All critical behaviors are testable programmatically:
- Auth bypass: verified by grep (zero occurrences) and 20 passing tests
- Startup validation: code paths visible and correct in `main.py`
- CORS configuration: logic verified by code review
- Docker artifacts: verified by file contents

---

### Gaps Summary

One gap blocks full goal achievement:

**OCR-02: Tesseract not installed in Docker image** — `requirements-prod.txt` correctly includes `pytesseract==0.3.10` (the Python wrapper), and `rasteriser.py` correctly calls it on Linux. However, `pytesseract` is only a wrapper: it requires the `tesseract-ocr` system binary to be installed via `apt-get`. Without a Dockerfile that runs `RUN apt-get install -y tesseract-ocr tesseract-ocr-eng`, the production Docker image will fail OCR on Linux with `tesseract is not installed` or similar errors. The 04-02-PLAN.md summary itself acknowledges this as deferred.

The fix is a single Dockerfile instruction, likely appropriate for Phase 5 (Containerization) which already owns `CONT-01` and `CONT-04`. The REQUIREMENTS.md traceability and `[x]` checkbox for OCR-02 should be corrected to reflect it is pending the Dockerfile.

All other phase deliverables — security hardening, CORS validation, JWT enforcement, test migration, dockerignore, and the code-level OCR backend — are fully implemented and working.

---

_Verified: 2026-03-19_
_Verifier: Claude (gsd-verifier)_
