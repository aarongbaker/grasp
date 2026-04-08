# Roadmap: grasp — Hardening Milestone

## Overview

The backend pipeline is complete. This milestone hardens it for production by closing four categories of risk in order of impact: first add missing tests (zero behavioral risk), then fix the two confirmed correctness bugs, then shut the security surface, then profile and optimize performance. Each phase gates the next — tests reveal existing bugs, bug fixes unblock safe security changes, security lands before performance work that raises throughput.

## Phases

- [ ] **Phase 1: Test Infrastructure** - Add missing test coverage for admin routes, health endpoint, and Celery task failure paths; extract shared test helper
- [ ] **Phase 2: Correctness Fixes** - Fix AsyncOpenAI connection leak and finalise_session() race condition; parallelize embedding fallback
- [ ] **Phase 3: Security Surface Closure** - Add rate limiting, kitchen config bounds validators, and RAG user_id assertion; cache RAG context
- [ ] **Phase 4: Performance** - Profile scheduler O(n²) and resolve if confirmed; gate on Phase 1 kitchen edge case tests

## Phase Details

### Phase 1: Test Infrastructure
**Goal**: Every production code path in admin routes, the health endpoint, and Celery task failure handling has test coverage; shared test infrastructure is extracted so Phase 2–4 tests can build on it
**Depends on**: Nothing (first phase)
**Requirements**: TEST-01, TEST-02, TEST-03, TEST-04, TEST-05, TEST-06
**Success Criteria** (what must be TRUE):
  1. `pytest -m "not integration" -v` reports 130+ passing tests with zero failures
  2. Admin invite CRUD endpoints (admin/non-admin/unauthenticated callers) are covered by tests that confirm correct HTTP status codes
  3. Health check endpoint test confirms liveness under normal DB connection and the degraded/failure path
  4. `_run_pipeline_async` task early-exit, graph exception, and ValidationError paths each have a dedicated test that runs without a real Celery broker
  5. Kitchen edge case tests (zero burners, missing config, invalid descriptors) exist and pass, serving as regression gate for Phase 4 scheduler work
**Plans**: TBD

### Phase 2: Correctness Fixes
**Goal**: The two confirmed production bugs are closed — AsyncOpenAI connections no longer accumulate in Celery workers, and finalise_session() cannot double-write via a TOCTOU race; embedding fallback is parallelized as a co-located improvement
**Depends on**: Phase 1
**Requirements**: BUG-01, BUG-02, PERF-01
**Success Criteria** (what must be TRUE):
  1. `embed_and_upsert_chunks` opens exactly one AsyncOpenAI client per invocation using a context manager, verified by a test that inspects client instantiation count
  2. `finalise_session()` issues a single `SELECT ... FOR UPDATE` as its only DB read; a concurrent-writer test confirms the CANCELLED guard holds under race conditions
  3. Embedding fallback loop uses `asyncio.gather(return_exceptions=True)` with `Semaphore(10)`; a test with a partial-failure mock confirms failed chunks are isolated and do not abort the batch
  4. All 99 pre-existing tests remain green
**Plans**: TBD

### Phase 3: Security Surface Closure
**Goal**: The three security gaps are closed — `POST /sessions` is rate-limited per authenticated user, kitchen config inputs are bounds-validated before reaching the scheduler, and RAG chunk retrieval asserts ownership before use; RAG context is cached to eliminate N+1 queries
**Depends on**: Phase 2
**Requirements**: SEC-01, SEC-02, SEC-03, PERF-02
**Success Criteria** (what must be TRUE):
  1. `POST /sessions` returns HTTP 429 after exceeding the configured per-user rate limit; a separate authenticated user is not affected by the first user's limit exhaustion
  2. Submitting a kitchen config with `max_burners` above the allowed ceiling or an equipment count above the allowed maximum returns a Pydantic validation error before the pipeline executes
  3. A RAG retrieval that returns a chunk whose `user_id` metadata does not match the requesting user causes that chunk to be logged and dropped silently — no crash, no cross-user data leak
  4. Pipeline runs with multiple recipes issue one Pinecone query per unique context key rather than one per recipe step; N+1 query pattern is eliminated
**Plans**: TBD

### Phase 4: Performance
**Goal**: Scheduler slot-finding performance is confirmed adequate or improved via interval-based indexing; all performance work is gated on profiling results from Phase 1 regression tests
**Depends on**: Phase 3
**Requirements**: PERF-03
**Success Criteria** (what must be TRUE):
  1. A profiling run with 10+ recipes and 50+ steps is executed and the result is recorded; if O(n²) growth is confirmed, the `_IntervalIndex` replacement is implemented and verified against Phase 1 kitchen edge case tests with identical fixture outputs
  2. If the existing `_IntervalIndex` already resolves the worst-case path, that is documented and Phase 4 is marked complete without a code change
**Plans**: TBD

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Test Infrastructure | 0/TBD | Not started | - |
| 2. Correctness Fixes | 0/TBD | Not started | - |
| 3. Security Surface Closure | 0/TBD | Not started | - |
| 4. Performance | 0/TBD | Not started | - |
