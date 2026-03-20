---
phase: 04-security-pre-deploy-hardening
plan: "01"
subsystem: auth
tags: [security, auth, jwt, cors, hardening]
dependency_graph:
  requires: []
  provides: [JWT-only-auth, CORS-production-validation]
  affects: [core/auth.py, main.py, tests/test_auth.py, tests/test_api_routes.py]
tech_stack:
  added: []
  patterns: [JWT-only-auth, fail-loud-production-validation, generic-error-messages]
key_files:
  created: []
  modified:
    - core/auth.py
    - main.py
    - tests/test_auth.py
    - tests/test_api_routes.py
decisions:
  - "Generic 401 message extracted to _AUTH_ERROR constant to ensure all error paths return identical text"
  - "CORS check uses set comparison (<=) to catch subsets of dev origins, not just exact match"
metrics:
  duration: 2 minutes
  completed: "2026-03-20"
  tasks_completed: 3
  files_modified: 4
---

# Phase 04 Plan 01: Auth Hardening and CORS Production Validation Summary

**One-liner:** JWT-only authentication with generic 401 messages and CORS startup validation that blocks dev-default origins in production.

## What Was Built

Hardened the API for public deployment by removing the X-User-ID auth bypass, standardizing all 401 error messages to a single generic string, adding CORS production validation in the lifespan hook, and migrating all tests to JWT-only auth.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Remove X-User-ID bypass and harden auth error messages | 1f9c835 | core/auth.py |
| 2 | Add CORS production validation and verify JWT secret check | 40afbeb | main.py |
| 3 | Migrate tests from X-User-ID to JWT-only auth | 1589728 | tests/test_auth.py, tests/test_api_routes.py |

## Decisions Made

1. **Generic 401 message extracted to `_AUTH_ERROR` constant** — Ensures all error paths (expired token, malformed token, missing token, invalid sub claim, refresh token as access token) return identical text. Eliminates accidental message divergence if paths are modified later.

2. **CORS check uses set subset comparison (`configured_origins <= _DEV_ORIGINS`)** — Catches the case where someone sets only one of the two dev origins rather than requiring an exact match. Prevents subtle misconfiguration where localhost:3000 alone passes the check.

## Test Results

All 20 tests in `test_auth.py` and `test_api_routes.py` pass. Breakdown:

- `test_auth.py`: 6 tests (removed 2 X-User-ID tests, added `test_jwt_bearer_auth_generic_error_message`)
- `test_api_routes.py`: 14 tests (replaced 2 X-User-ID tests with JWT equivalents, all others unchanged)

## Verification

```
grep -rn "X-User-ID|x_user_id" core/ tests/ api/  → zero matches
grep "Missing or invalid authentication token" core/auth.py  → _AUTH_ERROR = "Missing or invalid authentication token."
grep "CORS_ALLOWED_ORIGINS must be set" main.py  → present in lifespan
.venv/bin/python -m pytest tests/test_auth.py tests/test_api_routes.py -v  → 20 passed
```

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- core/auth.py: FOUND (x_user_id=0, X-User-ID=0, generic message present)
- main.py: FOUND (_DEV_ORIGINS present, RuntimeError on dev defaults in production)
- tests/test_auth.py: FOUND (6 tests, no legacy tests, generic error test present)
- tests/test_api_routes.py: FOUND (14 tests, JWT-only auth tests present)
- Commits: 1f9c835, 40afbeb, 1589728 — all present in git log
