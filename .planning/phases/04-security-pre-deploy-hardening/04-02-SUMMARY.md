---
phase: 04-security-pre-deploy-hardening
plan: 02
subsystem: infra
tags: [docker, ocr, tesseract, pytesseract, linux, requirements, pymupdf, apple-vision]

# Dependency graph
requires: []
provides:
  - .dockerignore excluding node_modules, .venv, .git, .planning, frontend, tests, docs
  - requirements-prod.txt as production dependency subset without pyobjc or dev packages
  - ingestion/rasteriser.py with three-tier OCR: Apple Vision (macOS) / Tesseract (Linux) / pymupdf (fallback)
affects: [05-docker-deploy, 06-railway-deploy]

# Tech tracking
tech-stack:
  added: [pytesseract==0.3.10]
  patterns:
    - Platform-aware OCR with _HAS_TESSERACT import-time detection flag
    - Conditional import inside function body for optional dependencies
    - requirements-prod.txt as production subset of requirements.txt

key-files:
  created:
    - .dockerignore
    - requirements-prod.txt
  modified:
    - ingestion/rasteriser.py

key-decisions:
  - "pytesseract conditional import at function level — graceful degradation when not installed (macOS dev)"
  - "Synthetic confidence 0.85 for Tesseract — reflects typical accuracy between Vision (~0.95) and pymupdf (0.7)"
  - "requirements-prod.txt as separate file rather than pip install flags — explicit production manifest"

patterns-established:
  - "Optional dependency pattern: module-level _HAS_X flag via try/except, conditional import inside function"
  - "Docker build context: .dockerignore keeps context to Python source only, frontend deploys separately"

requirements-completed: [CONT-02, CONT-03, OCR-01, OCR-02]

# Metrics
duration: 2min
completed: 2026-03-20
---

# Phase 4 Plan 02: Docker Build Context and Linux OCR Backend Summary

**.dockerignore for minimal build context + requirements-prod.txt without pyobjc + three-tier OCR (Apple Vision / Tesseract / pymupdf) in rasteriser.py**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-20T03:21:46Z
- **Completed:** 2026-03-20T03:22:07Z
- **Tasks:** 2 completed
- **Files modified:** 3

## Accomplishments
- Created .dockerignore excluding all dev/frontend/planning artifacts from Docker build context
- Created requirements-prod.txt as production-only dependency manifest: no pyobjc, pytest, httpx, streamlit; includes pytesseract==0.3.10
- Extended ingestion/rasteriser.py with _HAS_TESSERACT detection flag and _ocr_page_tesseract() function; OCR now selects Apple Vision on macOS, Tesseract on Linux, pymupdf as last-resort fallback on any platform

## Task Commits

Each task was committed atomically:

1. **Task 1: Create .dockerignore and production requirements file** - `7a16eb1` (chore)
2. **Task 2: Add Tesseract OCR backend to rasteriser** - `68565f0` (feat)

## Files Created/Modified
- `.dockerignore` - Docker build context exclusions: node_modules, .venv, .git, .planning, frontend, tests, docs, dev tools
- `requirements-prod.txt` - Production-only Python dependencies with pytesseract, without pyobjc/pytest/streamlit/httpx
- `ingestion/rasteriser.py` - Added _HAS_TESSERACT flag, _ocr_page_tesseract() function, three-branch OCR selection logic; updated module docstring

## Decisions Made
- pytesseract is imported conditionally at function level so rasteriser.py imports successfully on macOS dev machines without Tesseract installed
- Synthetic confidence 0.85 for Tesseract reflects its typical accuracy on clean cookbook scans — positioned between Apple Vision (~0.95) and pymupdf (0.7)
- requirements-prod.txt maintained as a standalone file rather than using pip markers, giving Docker an explicit production manifest

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. The tesseract-ocr system package must be present in the Docker image (handled in a subsequent Dockerfile task).

## Next Phase Readiness
- .dockerignore and requirements-prod.txt ready for Dockerfile authoring
- rasteriser.py will use Tesseract automatically once tesseract-ocr system package is installed in Docker image
- Apple Vision path preserved — macOS development unaffected

---
*Phase: 04-security-pre-deploy-hardening*
*Completed: 2026-03-20*
