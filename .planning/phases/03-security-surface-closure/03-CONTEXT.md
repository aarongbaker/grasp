# Phase 3: Security Surface Closure - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Close the scoped security hardening gaps already called out in the roadmap:
- make `POST /sessions` actually rate-limited by authenticated user, with a fallback policy for unauthenticated callers
- add strict kitchen-config bounds validation before invalid profile data reaches scheduling
- assert RAG ownership server-side on retrieved chunks before enrichment uses them
- eliminate the current per-recipe RAG retrieval N+1 pattern with a cache that stays inside one pipeline run

This phase does not add new product capabilities. It hardens existing backend contracts and keeps the current API shapes and pipeline topology intact.

</domain>

<decisions>
## Implementation Decisions

### Session Creation Rate Limiting
- **D-01:** `POST /sessions` uses a hybrid limit policy: authenticated callers are keyed by user identity, and unauthenticated callers fall back to IP-based throttling.
- **D-02:** The authenticated-user ceiling is `10/minute`.
- **D-03:** The unauthenticated IP fallback ceiling is `5/minute`.

### Kitchen Config Validation
- **D-04:** Validation covers both numeric bounds and cross-field invariants, not numeric ceilings alone.
- **D-05:** `max_burners` is capped at `10`.
- **D-06:** Maximum equipment count per user is `20`.
- **D-07:** Invalid relational kitchen data is rejected with a validation error; it is never silently normalized.
- **D-08:** Cross-field validation must reject configurations where `len(burners) > max_burners`.
- **D-09:** Cross-field validation must reject second-oven rack values when `has_second_oven` is false.

### RAG Ownership And Cache Policy
- **D-10:** If Pinecone returns a chunk whose ownership metadata does not match the requesting user, drop the chunk and log it server-side.
- **D-11:** Ownership mismatches do not fail the enrichment step and do not surface as user-visible pipeline errors.
- **D-12:** RAG context caching is scoped to a single pipeline run, keyed by session/query context rather than shared across sessions.

### the agent's Discretion
- Exact implementation shape for the hybrid slowapi key function and decorator wiring, as long as the policy above is enforced.
- Exact field-validator vs model-validator split for kitchen config, as long as the rejection behavior and ceilings above are preserved.
- Exact cache container and helper structure inside the enricher path, as long as cache scope remains per pipeline run only.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase Scope And Acceptance Criteria
- `.planning/ROADMAP.md` — defines the Phase 3 goal, success criteria, and dependency on Phase 2
- `.planning/REQUIREMENTS.md` — maps this phase to `SEC-01`, `SEC-02`, `SEC-03`, and `PERF-02`
- `.planning/PROJECT.md` — project-level constraints: no API shape changes, no graph topology changes, and no new dependencies by default

### Session Creation And Rate Limiting
- `app/api/routes/sessions.py` — `create_session()` route, current slowapi usage, and session lifecycle contract
- `app/main.py` — app-level limiter initialization and current remote-address keying
- `app/core/auth.py` — current authenticated user dependency behavior that the rate-limit keying must align with

### Kitchen Config Validation Surfaces
- `app/api/routes/users.py` — request models and kitchen update route currently enforcing only partial bounds
- `app/models/user.py` — persisted `KitchenConfig`, `BurnerDescriptor`, and `Equipment` models that need stronger validation
- `.planning/codebase/CONCERNS.md` — source concern calling out missing kitchen-config bounds and max equipment limits

### RAG Ownership And Cache Surfaces
- `app/graph/nodes/enricher.py` — `_retrieve_rag_context()` ownership filtering and the current per-recipe retrieval pattern
- `app/models/ingestion.py` — chunk metadata contract, including `user_id` and ownership-related Pinecone metadata
- `app/workers/tasks.py` — pipeline-run context available for session-scoped caching

### Existing Test Baseline
- `tests/test_api_routes.py` — route-level test harness for session and user endpoints
- `tests/test_enricher_integration.py` — existing enricher seam tests and likely home for ownership/cache regressions
- `tests/test_phase6_unit.py` — kitchen-config and burner-descriptor behavior already pinned in scheduler-focused tests

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/test_api_routes.py`: established FastAPI route test harness that can absorb session rate-limit and kitchen validation regressions.
- `tests/test_enricher_integration.py`: existing seam-level coverage around `_retrieve_rag_context()` and `rag_enricher_node()` without needing real API calls.
- `app/models/user.py` already uses Pydantic validators on `BurnerDescriptor`, so Phase 3 can extend the same model-driven validation style to `KitchenConfig`.

### Established Patterns
- Slowapi is already installed and used on routes, but current keying is IP-based; Phase 3 should reuse that stack rather than introducing a new limiter library.
- Backend request validation prefers Pydantic/SQLModel field and model validators, with route handlers returning validation errors rather than silently correcting bad input.
- Enricher failures are designed to degrade gracefully on a per-recipe basis; ownership mismatches should preserve that resilient behavior by dropping bad chunks rather than aborting the node.

### Integration Points
- `create_session()` in `app/api/routes/sessions.py` is the Phase 3 entry point for `SEC-01`.
- `UpdateKitchenRequest` in `app/api/routes/users.py` and `KitchenConfig` in `app/models/user.py` are the Phase 3 seam for `SEC-02`.
- `_retrieve_rag_context()` and `_enrich_single_recipe()` in `app/graph/nodes/enricher.py` are the seam for both `SEC-03` and `PERF-02`.

</code_context>

<specifics>
## Specific Ideas

- Keep the Phase 3 rate-limit fix narrow: change keying and policy, not the API contract or session-creation response body.
- Reject invalid kitchen profile structures explicitly instead of trying to repair them server-side.
- Treat ownership-mismatched RAG chunks as suspicious data that should be logged and discarded while letting enrichment continue with any valid remaining context.
- Keep the cache lifetime inside one pipeline run to avoid cross-session staleness or user-isolation risk.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 03-security-surface-closure*
*Context gathered: 2026-04-08*
