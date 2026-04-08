---
phase: 02-correctness-fixes
plan: 02
subsystem: ingestion
tags: [openai, embeddings, asyncio, pinecone, pytest]
requires:
  - phase: 02
    provides: lock-safe terminal session ownership so ingestion changes land on a stable correctness baseline
provides:
  - Context-managed AsyncOpenAI lifecycle for embedder calls
  - Bounded concurrent fallback embedding with partial-failure isolation
  - Full non-integration regression confirmation after Phase 2 changes
affects: [phase-02, ingestion, embeddings, worker-reliability, performance]
tech-stack:
  added: []
  patterns:
    - Embedder network clients are opened once per ingestion call and reused across batch and fallback requests
    - Fallback fan-out uses `asyncio.gather(return_exceptions=True)` plus `asyncio.Semaphore(10)` to keep partial failures non-fatal
key-files:
  created: []
  modified:
    - app/ingestion/embedder.py
    - tests/test_embedder.py
key-decisions:
  - "Kept the existing skip-on-failure ingestion contract by converting failed fallback embeddings to `None` instead of raising out of the batch."
  - "Shared the same AsyncOpenAI client across batch and fallback paths so bounded concurrency does not create one HTTP client per chunk."
patterns-established:
  - "Embedder lifecycle tests patch `openai` and `pinecone` through `sys.modules` so the function-level imports are exercised without network dependencies."
  - "Partial-failure concurrency tests assert only successful vectors are committed and upserted, preserving the ingestion job's resilient semantics."
requirements-completed: [BUG-01, PERF-01]
duration: 10min
completed: 2026-04-08
---

# Phase 02 Plan 02: Embedder Concurrency Summary

**The cookbook embedder now scopes one AsyncOpenAI client per ingestion call and parallelizes fallback chunk embedding with bounded, partial-failure-safe concurrency, all while keeping the full non-integration suite green.**

## Performance

- **Duration:** 10 min
- **Started:** 2026-04-08T20:58:06Z
- **Completed:** 2026-04-08T21:08:31Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Wrapped `embed_and_upsert_chunks()` in a single `async with AsyncOpenAI(...)` block so the OpenAI client lifecycle is explicit and bounded to one invocation.
- Replaced the sequential fallback loop with `asyncio.gather(return_exceptions=True)` guarded by `asyncio.Semaphore(10)` while preserving skip-on-failure behavior.
- Added focused embedder tests and confirmed the full `pytest -m "not integration" -v` suite stays green after the Phase 2 changes.

## Task Commits

Each task was committed atomically:

1. **Task 1: Scope AsyncOpenAI to the full embedder invocation and add lifecycle tests** - `0cbb1fa` (`fix`)
2. **Task 2: Parallelize fallback embedding with bounded gather semantics and close with the full regression gate** - `3898df2` (`perf`)

**Plan metadata:** pending metadata commit for this summary/state update.

## Files Created/Modified
- `app/ingestion/embedder.py` - scopes the OpenAI client per call and adds bounded concurrent fallback embedding
- `tests/test_embedder.py` - verifies one-client lifecycle, context reuse in fallback mode, and partial-failure isolation

## Decisions Made
- Used `return_exceptions=True` and converted exception results to skipped chunks so failed fallback requests do not abort the rest of the batch.
- Kept the commit/upsert contract unchanged: only successful embeddings become `CookbookChunk` rows and Pinecone vectors, and the function returns the count of successful chunks.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 2 now has focused regression coverage for both the session-locking fix and the embedder lifecycle/concurrency work.
- The full non-integration gate passed with `352 passed, 26 skipped, 11 deselected`, so Phase 2 is ready for final closeout/verification.

## Self-Check: PASSED

---
*Phase: 02-correctness-fixes*
*Completed: 2026-04-08*
