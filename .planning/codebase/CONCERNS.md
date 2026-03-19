# Codebase Concerns

**Analysis Date:** 2026-03-18

## Tech Debt

**File Upload Size Limits Unvalidated:**
- Issue: Cookbook file uploads lack enforced size limits during ingestion. Upload endpoint accepts files without pre-validation or streaming safeguards.
- Files: `api/routes/ingest.py` (lines 40-70)
- Impact: Potential for OOM attacks or excessive memory consumption on large PDF ingestion. No protection against malicious uploads.
- Fix approach: Add `Content-Length` header validation before reading files, implement streaming PDF processing with `PyMuPDF`, add per-file size limits to settings.

**TODO: File Storage Architecture:**
- Issue: Line 51 in `api/routes/ingest.py` notes "V2 should store to object storage and pass a reference instead." Currently all files are read into memory during processing.
- Files: `api/routes/ingest.py:51`
- Impact: Scalability bottleneck; memory usage grows with concurrent uploads. No audit trail of ingested files.
- Fix approach: Migrate to S3/GCS storage, pass presigned URLs to ingestion pipeline, keep local filesystem for development only.

**Session State Serialization Risk:**
- Issue: `Session.concept_json` and `Session.result_*` columns store Python `dict`/`list` directly via SQLAlchemy JSON type. No validation that these structures match their Pydantic schemas at read time.
- Files: `models/session.py` (lines 37, 45-46)
- Impact: Schema migrations could leave incompatible JSON in DB; reads fail silently or with unclear errors. No validation on deserialization.
- Fix approach: Add `field_validator` to `Session` model to call `.model_validate()` on JSON fields at read time, or migrate to `JSONB` with schema constraints.

## Known Bugs

**Bare Exception Handlers Suppress Real Errors:**
- Symptoms: Multiple `except Exception:` clauses swallow all error types indiscriminately, making debugging production failures difficult.
- Files: `api/routes/sessions.py:125, 162, 202` | `core/status.py:110` | `graph/nodes/validator.py:56` | `graph/nodes/enricher.py:120` | `graph/nodes/dag_builder.py:110` | `graph/nodes/generator.py:229` | `graph/nodes/dag_merger.py:408` | `graph/nodes/renderer.py:297, 349`
- Trigger: Any exception in the caught blocks logs nothing or falls back silently.
- Workaround: Add `logger.exception()` before bare `except` to preserve stack trace.
- Fix: Replace bare `except Exception` with specific exception types or at minimum `except Exception as e: logger.exception(...)`.

**Checkpoint State Read Fallback Not Tested:**
- Symptoms: `status_projection()` in `core/status.py:110` catches all exceptions and returns `SessionStatus.GENERATING` as safe default. If checkpoint DB is actually down, frontend polls indefinitely without knowing session is stalled.
- Files: `core/status.py:107-111`
- Trigger: Postgres connection pool exhaustion, network partition to checkpoint DB, or LangGraph service unavailable.
- Workaround: Monitor checkpoint DB availability separately; don't rely on frontend polling to detect outage.
- Fix: Emit explicit `STALLED` status or store last-known-good timestamp and timeout fallback after N minutes.

**Race Condition in Session Cancellation:**
- Symptoms: Between checking `session.status.is_in_progress` (line 116) and revoking Celery task (line 124), pipeline may transition to terminal state, leaving Celery revoke attempt on an already-dead task.
- Files: `api/routes/sessions.py:104-126`
- Trigger: Long-running pipeline nearing completion while cancel request arrives.
- Workaround: Celery revoke is idempotent; task may already be done, which is fine.
- Fix: More defensive — check task status before revoke, or wrap revoke in try-except (already done at line 125, but add logging).

## Security Considerations

**JWT Secret Default in Settings:**
- Risk: `jwt_secret_key` defaults to `"change-me-in-production"`. Startup validates this only for `production` env (line 52 in `main.py`), not for `staging` or custom deployments. Default secret is exposed in `core/settings.py:29`.
- Files: `main.py:51-59` | `core/settings.py:17, 29`
- Current mitigation: Runtime warning logged at startup if default is used; exception raised in production.
- Recommendations:
  - Reject default secret in all non-development environments (not just production).
  - Move secret default to environment-only (no hardcoded fallback in code).
  - Add Dockerfile/k8s secret validation at deployment time.

**Permissive CORS Configuration:**
- Risk: Default CORS allows all origins in development (`http://localhost:3000`, `http://localhost:8501`), but `allow_methods=["*"]` and `allow_headers=["*"]` with `allow_credentials=True` creates over-broad access surface.
- Files: `main.py:128-134` | `core/settings.py:26`
- Current mitigation: Only applies in development; production CORS must be configured via env var.
- Recommendations:
  - Explicitly list only required HTTP methods (POST, GET, OPTIONS, DELETE) instead of wildcard.
  - Validate CORS origins list length in settings validator (prevent DoS from unbounded list).

**Legacy X-User-ID Header Auth:**
- Risk: `core/auth.py:70-75` accepts `X-User-ID` header as fallback auth method. This is deprecated but active, allowing any request to impersonate a user by guessing UUIDs.
- Files: `core/auth.py:47-89`
- Current mitigation: Comment states "deprecated, will be removed" but no timeline given. No rate limiting per user_id.
- Recommendations:
  - Set removal date (e.g., v2.0) and log warnings when header is used.
  - Implement user-lookup rate limiting on X-User-ID header separately from JWT rate limiting.
  - Document in API spec that X-User-ID is for development only.

**Pinecone User Isolation Filter Bypass Risk:**
- Risk: Pinecone RAG query in `graph/nodes/enricher.py:99-104` filters by `user_id` in metadata. If Pinecone filter syntax is misunderstood or if metadata is corrupted, filter may not apply, leaking recipes across users.
- Files: `graph/nodes/enricher.py:99-104`
- Current mitigation: Filter is applied at Pinecone API level; graceful degradation returns `[]` on filter failure.
- Recommendations:
  - Add assertion in tests that filter is correctly applied; verify test data includes multi-user scenarios.
  - Log `user_id` and result count when filter returns zero results (could indicate filter failure).
  - Consider implementing server-side recipe filtering as secondary validation.

## Performance Bottlenecks

**Synchronous Socket Check for Redis at Startup:**
- Problem: `main.py:138-151` performs blocking TCP connect to Redis on app startup to decide rate limiter backend. If Redis is slow or network is congested, startup hangs.
- Files: `main.py:138-163`
- Cause: `socket.create_connection()` is synchronous and doesn't timeout efficiently; 2-second timeout can feel long at startup.
- Improvement path:
  - Move Redis connectivity check to async background task that initializes after app is ready.
  - Fall back to in-memory limiter immediately; upgrade to Redis lazily when ready.
  - OR: Accept in-memory limiter as permanent fallback for simple deployments (not a true problem).

**Unbounded Chunk Accumulation in State Machine:**
- Problem: `ingestion/state_machine.py:99-100` accumulates recipe sentences in `current_chunk` list without size limit. A single recipe section with 10,000+ sentences could exceed memory.
- Files: `ingestion/state_machine.py:85-172`
- Cause: Design keeps recipes whole to preserve RAG retrieval quality, but no safeguard against pathological recipes.
- Improvement path:
  - Add fallback word/sentence count limit even for recipe chunks (e.g., 5000 words absolute max).
  - Log warning when chunk exceeds safe size; split anyway rather than silently accumulating.
  - Test with adversarial cookbook containing single 50,000-word recipe.

**Embedder Batch Processing Error Recovery:**
- Problem: `ingestion/embedder.py:74-80` embeds chunks in batches of 50. If batch embedding fails, code falls back to per-chunk embedding without logging which chunks succeeded/failed, making re-ingestion retry semantics unclear.
- Files: `ingestion/embedder.py:74-80` (read past line 80 for full context)
- Cause: Graceful degradation swallows intermediate state; unclear which chunks are safely in Pinecone.
- Improvement path:
  - Log success count per batch.
  - Track per-chunk embed status in DB (add `embedded_at` timestamp to `CookbookChunk`).
  - On retry, skip already-embedded chunks.

**Status Projection Called on Every Session Poll:**
- Problem: `GET /sessions/{id}` calls `status_projection()` for in-progress sessions (line 160 in `api/routes/sessions.py`), which reads from LangGraph checkpoint every poll. No caching; frontend typically polls every 2 seconds.
- Files: `api/routes/sessions.py:136-166` | `core/status.py:97-127`
- Cause: Two-tier read is correct design, but checkpoint reads are expensive (network latency, Postgres round-trip).
- Improvement path:
  - Cache status in Redis with TTL (5-10 seconds) per session.
  - OR: Use SSE (Server-Sent Events) instead of polling; graph pushes status updates to connected clients.
  - Document polling interval expectations for frontend; 5-second interval is safer.

## Fragile Areas

**Global Graph Variable in main.py:**
- Files: `main.py:33-40`
- Why fragile: Single `_graph` module-level variable initialized once at startup. If re-import or tests run before lifespan, `get_graph()` raises `RuntimeError`. Circular import risks between `main.py` and route handlers.
- Safe modification: Never reimport main after startup; tests must use fixtures to inject graph. Document that routes must import `get_graph` inside route functions, not at module level.
- Test coverage: `tests/` likely has fixtures for graph; verify all tests use them and none import `_graph` directly.

**Error State Accumulation in GRASPState:**
- Files: `models/pipeline.py` (not read, but referenced by `graph/nodes/*`)
- Why fragile: Each node that catches an exception appends to `state["errors"]` list. If a node is added or removed, error list ordering/expectations break downstream. No schema validation of error structure.
- Safe modification: Always use `NodeError` model; validate in `error_router()` that all errors are properly formatted before routing. Add test that exercises each error path independently.
- Test coverage: `tests/test_phase3.py` likely covers error routing; verify it tests all node types and all error paths.

**LangGraph Checkpoint Dependency:**
- Files: `main.py:84-105` | `workers/tasks.py:59-60` | `graph/graph.py`
- Why fragile: Application requires both `AsyncPostgresSaver` (LangGraph production) and `MemorySaver` (fallback). Fallback masks real connectivity issues; tests that use MemorySaver don't exercise production checkpoint path.
- Safe modification: Keep fallback for tests, but in production, fail loudly if checkpoint init fails. Add explicit feature flag: `REQUIRE_CHECKPOINT_DB=true` for prod, `false` for dev.
- Test coverage: Add integration tests that explicitly use `AsyncPostgresSaver` with a test Postgres instance, separate from unit tests.

**Celery Task Visibility Timeout:**
- Files: `workers/tasks.py:28-34` | `api/routes/sessions.py:92-98`
- Why fragile: Celery task result is stored in Redis with default visibility/expiration. If result expires before `finalise_session()` completes, task status becomes unknown and frontend hangs.
- Safe modification: Document Celery result backend TTL; ensure it's longer than `celery_task_timeout` (600s by default). Or better: don't rely on Celery result storage; write completion status directly to DB.
- Test coverage: Add test that delays `finalise_session()` past result TTL and verifies session status is still correct.

## Scaling Limits

**In-Memory Rate Limiter Falls Back Silently:**
- Current capacity: Each FastAPI worker gets its own in-memory rate limiter dict. Limits do not cross processes.
- Limit: In a deployment with 4 workers, attacker can make 4x the limit by round-robining requests. Limits appear permissive from single worker's perspective.
- Scaling path: Always require Redis (remove in-memory fallback in production), or implement application-level request deduplication/windowing in Nginx/load balancer.

**Pinecone Vector Store Per-Chef Isolation:**
- Current capacity: Single Pinecone index shared across all users, filtered by `user_id`. As user count grows, index size grows, query latency increases.
- Limit: Pinecone free tier supports ~100k vectors; prod instance cost grows linearly with vectors stored.
- Scaling path: Partition Pinecone index by region/cohort, or migrate to self-hosted Weaviate/Milvus if vector store cost becomes prohibitive.

**Session State in PostgreSQL JSON Columns:**
- Current capacity: Each session stores `concept_json` and full pipeline state. Large cookbooks (100+ recipes) create large JSON payloads.
- Limit: Postgres JSON query/index performance degrades with column size; no sharding strategy defined.
- Scaling path: Implement JSON archival after 30 days; move old sessions to cold storage (S3); keep only recent 100 sessions in hot DB.

**Concurrent Celery Workers Limited to 4:**
- Current capacity: `celery_worker_concurrency = 4` in settings; supports 4 concurrent pipeline executions.
- Limit: Single-machine deployment can only handle 4 simultaneous sessions. Multi-machine workers require shared checkpointer, which itself becomes bottleneck.
- Scaling path: Consider async task queue (e.g., Temporal, Durable Functions) with built-in distributed coordination, or implement session queue with priority (premium users skip queue).

## Dependencies at Risk

**PyMuPDF (fitz) for PDF Extraction:**
- Risk: PyMuPDF is closed-source and licensing unclear for commercial use. Large PDF files sometimes extract corrupted text; state machine may receive garbage input.
- Impact: Licensing ambiguity could block deployment; corrupted extracts bypass validation and propagate through ingestion.
- Migration plan: Evaluate pdfplumber (open source, pure Python) as alternative. Benchmark extraction quality on corpus of test cookbooks.

**Anthropic Claude API Hard Dependency:**
- Risk: No fallback LLM if Claude API is down. Generator, Enricher, and Renderer all use `ChatAnthropic` directly.
- Impact: Any Claude outage stops all pipeline runs. No graceful degradation to cheaper model or cached recipes.
- Migration plan: Abstract LLM calls behind interface; support multiple providers (Claude + GPT-4). Cache successful enrichments per recipe; skip re-enrichment on retry.

**LangGraph PostgreSQL Checkpoint Schema:**
- Risk: LangGraph's checkpoint schema is internal; version upgrades may require migration. No schema migration tool in `main.py` lifespan.
- Impact: Upgrading LangGraph could break all running sessions.
- Migration plan: Vendor checkpoint schema; document version pinning for LangGraph. Test major version upgrades in staging before deploying.

## Missing Critical Features

**No Session Timeout / Maximum Duration:**
- Problem: A pipeline can run indefinitely if LLM calls hang. No max execution time enforced at application level.
- Blocks: Long-running sessions block Celery worker slots; no protection against resource exhaustion.
- Feature request: Add `max_duration_minutes` to `DinnerConcept`; cancel session if elapsed time exceeds limit. Emit warning at 80% of limit.

**No Cookbook Deletion / Invalidation:**
- Problem: Once a cookbook is ingested, chunks remain in Pinecone forever. No way to remove or deprecate a cookbook.
- Blocks: Users cannot fix ingestion errors; cannot revoke access to private recipes.
- Feature request: Add `DELETE /cookbooks/{id}` endpoint that removes all chunks for that book_id from Pinecone and marks them deleted in DB.

**No User Role-Based Access Control (RBAC):**
- Problem: Currently all users are equal; no concept of admin, viewer, or editor roles.
- Blocks: Cannot delegate session management or approve ingestions without full app access.
- Feature request: Add `role` field to `UserProfile`; gate ingest endpoint to admins only. Return only user's own sessions in list view.

**No API Rate Limiting Per-User:**
- Problem: Rate limiter in `main.py:155-162` is global (all users share limits). Heavily-used sessions can be starved by noisy users.
- Blocks: Multi-tenant deployments have no fairness guarantees.
- Feature request: Implement per-user rate limiting in Redis with user_id key. Tier limits based on subscription level.

## Test Coverage Gaps

**RAG Retrieval Edge Cases:**
- What's not tested: What happens when Pinecone returns zero results; when embedding API times out; when filter syntax is wrong.
- Files: `graph/nodes/enricher.py:71-122`
- Risk: Graceful degradation to LLM-only proceeds silently; frontend may not know that RAG was skipped.
- Priority: **High** — affects recipe quality; silent failure is worse than loud error.

**Graph Error Routing:**
- What's not tested: What happens when multiple nodes fail in sequence; when error count exceeds limits; when error_router itself fails.
- Files: `graph/router.py` (not read, but exercised by error cases in all nodes)
- Risk: Unclear whether pipeline continues or stops; error_router may route to wrong node on malformed errors.
- Priority: **High** — error paths are the only way production issues surface.

**Session Cancellation Race Conditions:**
- What's not tested: Cancellation during each phase of pipeline (generator, enricher, scheduler); cancellation after already-completed.
- Files: `api/routes/sessions.py:103-133`
- Risk: Cancelled session may still write results; or results may be lost if cancellation happens mid-commit.
- Priority: **Medium** — edge case, but affects user experience if scheduling mid-generation.

**Database Connection Pool Exhaustion:**
- What's not tested: Behavior when connection pool is full; concurrent requests beyond pool size.
- Files: `db/session.py` (not fully read)
- Risk: Requests hang or raise connection error; no clear feedback to client.
- Priority: **Medium** — load testing would catch this; not covered by unit tests.

**Multiuser RAG Isolation:**
- What's not tested: Multiple users ingesting cookbooks and querying simultaneously; filter enforcement.
- Files: `graph/nodes/enricher.py:99-104` | `ingestion/embedder.py:45-100`
- Risk: User A's recipes appearing in User B's enrichment results.
- Priority: **High** — security/privacy issue; must be tested explicitly.

---

*Concerns audit: 2026-03-18*
