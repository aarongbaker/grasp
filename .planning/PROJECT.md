# grasp — Hardening Milestone

## What This Is

grasp is a dinner party planning tool for private chefs and home cooks that uses LLM-driven menu generation, RAG-backed recipe retrieval, DAG-based schedule optimization, and a food costing engine. The backend pipeline (Phases 1–7) is fully complete. This milestone focuses on production hardening: closing test coverage gaps, addressing security vulnerabilities, fixing known bugs, and resolving performance bottlenecks identified in the post-Phase-7 codebase audit.

## Core Value

The pipeline must be reliable and defensible in production — every identified vulnerability patched, every critical code path tested, and the scheduler performant under real-world menu complexity.

## Requirements

### Validated

- ✓ LangGraph pipeline (generator → enricher → validator → dag_builder → dag_merger → renderer) — existing
- ✓ FastAPI REST API with JWT auth and session management — existing
- ✓ Celery + Redis async task queue for pipeline execution — existing
- ✓ PostgreSQL checkpointing with LangGraph PostgresSaver — existing
- ✓ Pinecone RAG with per-user vector isolation — existing
- ✓ Pydantic-validated domain models (RawRecipe → EnrichedRecipe → ValidatedRecipe → ScheduledStep) — existing
- ✓ Per-recipe recoverable error isolation in enricher, validator, dag_builder — existing
- ✓ Greedy list scheduler with resource conflict detection (HANDS, STOVETOP, OVEN, PASSIVE) — existing

### Active

- [ ] Test coverage: admin route endpoints (admin.py), health check endpoint
- [ ] Test coverage: Celery task retry logic, failure callbacks, timeout handling (tasks.py)
- [ ] Test coverage: equipment CRUD operations and equipment-constraint unlock in dag_merger
- [ ] Test coverage: kitchen config edge cases (zero burners, missing config, invalid descriptors)
- [ ] Security: Pydantic bounds validators on kitchen config (max equipment count, burner limits)
- [ ] Security: Rate limiting on POST /sessions endpoint (slowapi integration)
- [ ] Security: Server-side assertion that RAG-retrieved chunks belong to requesting user
- [ ] Bug fix: AsyncOpenAI client resource leak in app/ingestion/embedder.py (line 72) — use context manager
- [ ] Bug fix: finalise_session() race condition in app/core/status.py (lines 52–56) — SELECT FOR UPDATE
- [ ] Performance: Replace O(n²) scheduler iteration loop with interval-based time-slot search in dag_merger.py
- [ ] Performance: Parallelize embedding fallback in embedder.py using asyncio.gather with bounded concurrency
- [ ] Performance: Batch or cache RAG context retrieval in enricher.py to eliminate N+1 Pinecone queries

### Out of Scope

- Frontend UI changes — addressed in a separate UI milestone
- Cost estimation before pipeline run — a new feature, not hardening
- Session cancellation with Celery task revocation — new feature requiring significant new work
- Dietary restriction enforcement — new feature, not hardening
- LangGraph checkpoint migration strategy — tracked but not actioned this milestone

## Context

- Codebase map available at `.planning/codebase/` (generated 2026-04-08)
- All concerns sourced from `.planning/codebase/CONCERNS.md`
- Backend is the only focus; frontend exists but is not in scope
- No specific external deadline — milestone is overdue general maintenance
- Python 3.12, FastAPI 0.111.0, LangGraph 1.0.10, LangChain 1.2.10, Pydantic 2.7.4, slowapi 0.1.9 already installed
- Test runner: `pytest -m "not integration" -v` (99 existing tests must continue to pass)

## Constraints

- **Compatibility**: Existing 99 tests must not regress — any refactor must keep the test suite green
- **No topology changes**: graph.py topology is locked after Phase 3; new tests and fixes must not alter the LangGraph StateGraph structure
- **No new dependencies**: Prefer using already-installed libraries (slowapi, tenacity, asyncpg) before adding new packages
- **Backward compatibility**: API response shapes must not change — frontend depends on them

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| SELECT FOR UPDATE for finalise_session race | Prevents double-write without requiring distributed locks | — Pending |
| asyncio.gather with semaphore for embedding fallback | Bounded parallelism avoids overwhelming OpenAI rate limits | — Pending |
| Interval-based time-slot search in scheduler | Eliminates O(n²) worst case; compatible with existing greedy approach | — Pending |

---
*Last updated: 2026-04-08 after initialization*
