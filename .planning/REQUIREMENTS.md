# Requirements: grasp — Hardening Milestone

**Defined:** 2026-04-08
**Core Value:** The pipeline must be reliable and defensible in production — every identified vulnerability patched, every critical code path tested, and the scheduler performant under real-world menu complexity.

## v1 Requirements

### Test Coverage

- [ ] **TEST-01**: Admin route endpoints tested — invite flow endpoints in admin.py, used in production, zero current coverage
- [ ] **TEST-02**: Health check endpoint tested — DB query liveness contract in health.py, critical for monitoring
- [ ] **TEST-03**: Celery `_run_pipeline_async` unit tested — failure callbacks and timeout handling without real broker
- [ ] **TEST-04**: Celery `_ingest_async` unit tested — failure behavior and error propagation
- [ ] **TEST-05**: Equipment CRUD operations tested — create/delete routes and scheduler constraint unlock in dag_merger
- [ ] **TEST-06**: Kitchen config edge cases tested — zero burners, missing config, invalid descriptors serve as regression suite for scheduler

### Security

- [ ] **SEC-01**: Rate limiting enforced on `POST /sessions` — shared slowapi singleton extracted to `app/core/limiter.py`, per-user JWT key_func applied (current disconnected Limiter instance never applies)
- [ ] **SEC-02**: Kitchen config Pydantic bounds validators added — max equipment count, burner count upper limits, capacity number ranges enforced via `ge`/`le` field validators
- [ ] **SEC-03**: RAG chunk user_id assertion added — server-side metadata comparison confirms retrieved chunks belong to requesting user, zero additional DB queries

### Bug Fixes

- [ ] **BUG-01**: `AsyncOpenAI` client resource leak fixed — `async with AsyncOpenAI(...) as client:` wraps entire `embed_and_upsert_chunks` function body in `app/ingestion/embedder.py` (line 72)
- [ ] **BUG-02**: `finalise_session()` race condition fixed — single `select(...).with_for_update()` replaces `db.get()` + `db.refresh()` pair in `app/core/status.py`; same locking applied to cancellation PATCH handler in `app/api/routes/sessions.py`

### Performance

- [ ] **PERF-01**: Embedding fallback parallelized — `asyncio.gather` with `asyncio.Semaphore` and `return_exceptions=True` replaces sequential per-chunk fallback loop in `app/ingestion/embedder.py` (lines 103–112); done alongside BUG-01
- [ ] **PERF-02**: RAG context cache added in enricher — Pinecone retrieval results cached per session/recipe combination to eliminate N+1 queries in `app/graph/nodes/enricher.py` (lines 338–358)
- [ ] **PERF-03**: Scheduler O(n²) investigated and resolved — profile stovetop slot scan in `app/graph/nodes/dag_merger.py`; replace linear `burner_intervals` scan with `_IntervalIndex` per burner if profiling confirms O(n²) remains; kitchen edge case tests (TEST-06) serve as regression suite

## v2 Requirements

### Missing Features (deferred)

- **FEAT-01**: Session cancellation with Celery task revocation — new feature requiring significant new work
- **FEAT-02**: Cost estimation before pipeline run — dry-run mode with token cost preview
- **FEAT-03**: Dietary restriction enforcement — enum-based validation with post-generation recipe check

### Infrastructure (deferred)

- **INFRA-01**: Base64 PDF → object storage migration (S3/GCS reference passing)
- **INFRA-02**: LangGraph checkpoint migration strategy for major version upgrades
- **INFRA-03**: PgBouncer / explicit connection pool configuration

## Out of Scope

| Feature | Reason |
|---------|--------|
| Frontend UI changes | Separate UI milestone |
| LangGraph topology changes | graph.py locked after Phase 3; not a hardening concern |
| Admin invite feature work | Already in production; TEST-01 covers tests, not new features |
| Enricher failure threshold config | New feature complexity, not hardening |
| Pinecone abstract VectorStore interface | Architecture refactor, not a hardening fix |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| TEST-01 | Phase 1 | Pending |
| TEST-02 | Phase 1 | Pending |
| TEST-03 | Phase 1 | Pending |
| TEST-04 | Phase 1 | Pending |
| TEST-05 | Phase 1 | Pending |
| TEST-06 | Phase 1 | Pending |
| BUG-01 | Phase 2 | Pending |
| BUG-02 | Phase 2 | Pending |
| PERF-01 | Phase 2 | Pending |
| SEC-01 | Phase 3 | Pending |
| SEC-02 | Phase 3 | Pending |
| SEC-03 | Phase 3 | Pending |
| PERF-02 | Phase 3 | Pending |
| PERF-03 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 14 total
- Mapped to phases: 14
- Unmapped: 0 ✓

---
*Requirements defined: 2026-04-08*
*Last updated: 2026-04-08 after initial definition*
