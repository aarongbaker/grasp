# Codebase Concerns

**Analysis Date:** 2026-04-08

## Tech Debt

**Base64 PDF encoding in ingestion task queue:**
- Issue: PDFs are base64-encoded and passed as strings through Celery message queue rather than stored externally
- Files: `app/api/routes/ingest.py` (line 421-425)
- Impact: Scales poorly with large cookbooks; entire PDF lives in queue memory; limits queue throughput
- Fix approach: Migrate to object storage (S3/GCS) with reference passing. Celery task receives object key instead of encoded bytes.

**Oversized graph nodes:**
- Issue: Multiple nodes exceed 1000 lines with complex algorithmic logic
- Files: `app/graph/nodes/dag_merger.py` (1373 lines), `app/graph/nodes/enricher.py` (744 lines), `app/graph/nodes/generator.py` (928 lines)
- Impact: Difficult to test individual functions; high coupling; harder to refactor
- Fix approach: Extract pure utility functions into separate modules; break down monolithic scheduling loop into smaller, testable pieces

**Hardcoded temperature tolerance constant:**
- Issue: `TEMP_TOLERANCE_F = 15` is hardcoded in scheduling loop without configuration
- Files: `app/graph/nodes/dag_merger.py` (line 590)
- Impact: Users cannot adjust oven temperature compatibility; future kitchen profiles may need different tolerances
- Fix approach: Move to kitchen config or user profile settings; pass as parameter through scheduling pipeline

## Known Bugs

**AsyncOpenAI client not properly scoped:**
- Issue: `AsyncOpenAI` client created inside embedder function but never explicitly closed
- Files: `app/ingestion/embedder.py` (line 72)
- Impact: Client resources may not be released cleanly; potential connection leaks on long-running embedding jobs
- Workaround: Celery task terminates process after each job, releasing resources
- Fix approach: Use context manager or async context exit handler to ensure cleanup

**Database transaction isolation during checkpoint reads:**
- Issue: `finalise_session()` refreshes session from DB to detect concurrent cancellation, but race condition still possible between refresh and commit
- Files: `app/core/status.py` (line 52-56)
- Impact: In rare cases, both pipeline finalization and cancellation could write conflicting states
- Fix approach: Use database-level locking (SELECT FOR UPDATE) or transaction-scoped isolation

## Security Considerations

**Insufficient input validation on kitchen config:**
- Risk: Equipment list, burner descriptors, and capacity numbers accepted from user profile without bounds checking
- Files: `app/graph/nodes/dag_merger.py` (line 1103-1108), `app/api/routes/users.py` (line 186-190, 234-240)
- Current mitigation: Pydantic models define field types but not numeric ranges or string length limits
- Recommendations: Add validators for max equipment count, burner count upper limits; enforce kitchen config schema more strictly

**User ID not validated when accessing cookbook RAG context:**
- Risk: RAG retrieval filters by `user_id` via metadata, but if metadata bypass occurs, user could access other users' cookbooks
- Files: `app/graph/nodes/enricher.py` (line 338-350)
- Current mitigation: Pinecone vector store metadata filtering; user_id isolation at embedding layer
- Recommendations: Add server-side assertion that retrieved chunks belong to requesting user; log access patterns

**No rate limiting on session creation:**
- Risk: User can POST /sessions endpoint repeatedly without throttle; potential DOS
- Files: `app/api/routes/sessions.py` (no rate limit decorator observed)
- Current mitigation: Celery task queue may naturally throttle, but not guaranteed
- Recommendations: Add rate limit middleware (e.g., slowapi); configure per-user session quota

## Performance Bottlenecks

**10,000 iteration safety valve in resource scheduler:**
- Problem: Scheduling loop attempts up to 10,000 iterations to find valid time slot; worst case is O(n²) per step
- Files: `app/graph/nodes/dag_merger.py` (line 592, 674, 860)
- Cause: Greedy list scheduler without constraint propagation; no look-ahead heuristics
- Improvement path: Implement smarter time-slot search (e.g., binary search over conflict intervals); cap iterations and fail gracefully; consider alternative scheduling algorithms (constraint programming) for future versions

**Synchronous OpenAI embedding fallback blocks event loop:**
- Problem: If batch embedding fails, falls back to per-chunk embedding in a loop with no parallelism
- Files: `app/ingestion/embedder.py` (line 103-112)
- Cause: Sequential embedding attempt without `asyncio.gather()`
- Improvement path: Parallelize fallback embedding with limited concurrency; implement exponential backoff with jitter

**N+1 query in enricher RAG context retrieval:**
- Problem: Each recipe's RAG context retrieved sequentially; could batch retrieve if Pinecone query API supports it
- Files: `app/graph/nodes/enricher.py` (line 620-630 per-recipe retrieval in gather)
- Cause: Pinecone Python client may not support batch query; individual queries have per-request latency
- Improvement path: Verify Pinecone API capabilities; batch retrieve if possible; cache cookbook context across recipes

**Full checkpoint serialization on every state update:**
- Problem: LangGraph checkpointer persists entire state to PostgreSQL on each node completion; state can include full validated recipes
- Files: `app/workers/tasks.py` (line 45-47)
- Cause: LangGraph design; all state stored as JSON blob
- Improvement path: Implement thin checkpointer that stores only minimal state (IDs/keys) and fetches full objects on-demand; or partition state into hot/cold tiers

## Fragile Areas

**DAG cycle detection relies on external library (NetworkX):**
- Files: `app/graph/nodes/dag_builder.py` (line 40-50)
- Why fragile: Cyclic dependency passing validation (Pydantic) but caught by graph construction; if NetworkX changes cycle detection behavior, could break
- Safe modification: Write unit tests for known-cyclic graph fixtures; validate against NetworkX API stability
- Test coverage: `tests/test_phase3.py` (Run 3) covers cyclic case; `tests/test_phase6_unit.py` has cycle fixtures

**Oven temperature conflict resolution metadata structure:**
- Files: `app/graph/nodes/dag_merger.py` (line 649-672)
- Why fragile: Conflict metadata includes `remediation` object with nested suggestions; frontend parsing tightly coupled to exact schema
- Safe modification: Lock schema version; test that metadata round-trips through JSON and Pydantic validation
- Test coverage: `tests/test_oven_temp_conflict.py` covers temperature conflict detection

**Session status projection derivation order matters:**
- Files: `app/core/status.py` (line 117-126)
- Why fragile: Status determined by which fields are populated; if a node is re-run or checkpoint is corrupted, status may regress
- Safe modification: Add explicit status field to GRASPState instead of deriving from presence of fields; log state transitions
- Test coverage: `tests/test_status_projection.py` validates derivation logic

**Concurrent enrichment gather without error propagation bounds:**
- Files: `app/graph/nodes/enricher.py` (line 717-740)
- Why fragile: If even one recipe fails, entire enrichment fails as fatal; partial recovery not possible
- Safe modification: Allow N recipes to fail before fatal; expose failure thresholds in config
- Test coverage: `tests/test_enricher_integration.py` has partial failure cases

## Scaling Limits

**Single oven capacity bottleneck:**
- Current capacity: 1 oven (or 2 with `has_second_oven` flag)
- Limit: Menus with 5+ dishes using overlapping oven windows will deadlock; error "Cannot schedule ... after 10,000 iterations"
- Scaling path: Expand kitchen config to support N ovens; update conflict detection to track per-oven temps; allow users to rent/add commercial equipment

**RAG vector store per-user isolation:**
- Current capacity: ~10k chunks per user typical; Pinecone can handle ~1M+ chunks per index
- Limit: If many users ingest large cookbooks simultaneously, Pinecone embedding throughput exhausted; timeouts cascade
- Scaling path: Implement embedding queue with exponential backoff; add Pinecone index sharding by user cohort; cache embeddings

**Celery task queue serialization overhead:**
- Current capacity: Base64-encoded PDF in message queue; typical PDF 5MB → 7MB in queue
- Limit: Broker memory exhausted if 100+ concurrent ingestion jobs queued (700MB footprint)
- Scaling path: Migrate PDFs to object storage (issue noted above); queue only reference IDs

**Database connection pool:**
- Current: SQLAlchemy AsyncSession with default pool (NullPool for async)
- Limit: No explicit pool size configuration; high concurrency (100+ concurrent sessions) may exhaust connections
- Scaling path: Add explicit pool size configuration; implement connection pooling at DB layer (PgBouncer); monitor active connections

## Dependencies at Risk

**Pinecone Python SDK hard dependency:**
- Risk: Pinecone API changes could break RAG retrieval; no fallback if Pinecone unavailable
- Impact: If Pinecone service degraded, enricher fails (recoverable error); graceful degradation returns empty RAG context
- Migration plan: Extract Pinecone behind abstract VectorStore interface; implement in-memory mock for dev; prepare migration path to alternative vector DB (Weaviate, Milvus)

**LangGraph PostgreSQL checkpointer:**
- Risk: Checkpointer relies on LangGraph's unstable internal API; versions 0.1.x → 0.2.x had breaking changes
- Impact: If LangGraph version upgraded, checkpoint schema may be incompatible; existing sessions unrecoverable
- Migration plan: Pin LangGraph version explicitly; before upgrade, implement migration script to export/re-import checkpoints; test against LangGraph upgrade path

**Custom OpenAI Structured Output for RawRecipe:**
- Risk: `model=gpt-4-turbo` with `response_format={"type": "json_schema"}` may change in Claude/OpenAI API
- Impact: If schema validation changes, generator fails; recipes malformed
- Migration plan: Validate output against Pydantic model client-side; implement fallback to gpt-4-mini for retries; document minimum API version

## Missing Critical Features

**No session cancellation cleanup:**
- Problem: User can cancel session via PATCH /sessions/{id} but in-flight Celery task continues consuming resources
- Blocks: Long-running ingestion jobs can't be stopped; large PDFs may process for 10+ minutes even after user cancels
- Fix approach: Implement Celery task revocation with cleanup hooks; check cancellation flag in long-running loops; add timeout envelope

**No cost estimation before running pipeline:**
- Problem: Users generate menus with unknown LLM token cost; can rack up $50+ for a complex 5-course dinner
- Blocks: No cost control; users can't preview spend before committing
- Fix approach: Implement dry-run mode that estimates tokens without LLM execution; show cost breakdown in preview

**No user-defined dietary restrictions custom validation:**
- Problem: Dietary restrictions are free-text strings; no enforcement that generated recipes match user restrictions
- Blocks: Generated menu may include dishes incompatible with stated dietary needs
- Fix approach: Add dietary restriction enum with validation rules; post-validate generated recipes against restrictions; re-run generator on mismatch

## Test Coverage Gaps

**Untested API routes for admin and health:**
- What's not tested: `/api/v1/admin/*` invite endpoints; `/api/v1/health` endpoint
- Files: `app/api/routes/admin.py`, `app/api/routes/health.py`
- Risk: Admin invite flow could be broken without detection; health check DB query could deadlock
- Priority: High (admin invite used in production; health check critical for monitoring)

**Untested Celery task initialization:**
- What's not tested: Task retry logic, failure callbacks, timeout handling
- Files: `app/workers/tasks.py` (run_grasp_pipeline, ingest_cookbook)
- Risk: Task could hang silently; retries may create duplicate sessions
- Priority: High (production traffic runs through these)

**Untested user equipment management:**
- What's not tested: Equipment CRUD operations; equipment unlocks in scheduling
- Files: `app/api/routes/users.py` (POST/DELETE equipment); `app/graph/nodes/dag_merger.py` (equipment intervals)
- Risk: Equipment constraints could be silently ignored; data corruption on delete
- Priority: Medium (feature used by power users only)

**Untested kitchen profile edge cases:**
- What's not tested: Zero burners; missing kitchen config; invalid burner descriptors
- Files: `app/graph/nodes/dag_merger.py` (line 677-696); `app/api/routes/users.py` (PATCH /kitchen)
- Risk: Scheduler crashes or produces invalid schedules with edge case configs
- Priority: Medium (most users have standard kitchen config, but edge cases possible)

**Untested frontend session refresh race conditions:**
- What's not tested: User reloads page while pipeline in-flight; stale session status cached in browser
- Files: `frontend/src/pages/SessionDetailPage.tsx` (polling logic)
- Risk: UI shows "FAILED" while pipeline still running; user retries unnecessarily
- Priority: Low (UI has reasonable default polling interval, but potential flakiness in fast networks)

---

*Concerns audit: 2026-04-08*
