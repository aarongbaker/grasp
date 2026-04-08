# Phase 3: Security Surface Closure - Research

**Researched:** 2026-04-08
**Status:** Ready for planning

## Scope Anchors

- `SEC-01`: `POST /sessions` must be rate-limited by authenticated user identity, with IP fallback only for unauthenticated callers.
- `SEC-02`: kitchen config inputs need explicit numeric ceilings and cross-field invariants before invalid profile data reaches scheduling.
- `SEC-03`: RAG retrieval must assert returned chunk ownership server-side before enrichment uses the metadata payload.
- `PERF-02`: enricher RAG context retrieval should cache within a single pipeline run to eliminate repeated per-recipe Pinecone calls for equivalent lookups.

## Current Code Surface

### Session creation and rate limiting

- `app/api/routes/sessions.py` already decorates `create_session()` with `@limiter.limit("30/minute")`, but the module-level limiter is created with `get_remote_address`, so the effective key is IP-based rather than authenticated-user based.
- `app/main.py` also creates an application limiter with the same `get_remote_address` default and a Redis-backed or in-memory fallback depending on settings.
- `create_session()` already requires `current_user`, so authenticated-user keyed throttling can stay entirely within the current auth contract.
- Existing route coverage in `tests/test_api_routes.py` covers `create_session()` behavior, and `tests/test_middleware.py` already exercises slowapi response behavior in a smaller app harness.

### Kitchen config validation

- `app/api/routes/users.py` currently enforces only route-request numeric bounds: `max_burners <= 10`, rack counts `<= 6`, and typed burner lists.
- `app/models/user.py` `KitchenConfig` persists the actual data but has no bounds or cross-field validators beyond `BurnerDescriptor.burner_id` non-empty validation.
- `Equipment` records are separate rows, so the “max equipment count” rule is enforced at route/service level rather than as a pure `KitchenConfig` field bound.
- Existing tests in `tests/test_api_routes.py` and `tests/test_phase6_unit.py` already pin burner descriptor round-trips and kitchen-config consumption, so Phase 3 should extend those seams instead of inventing new endpoint shapes.

### RAG ownership and per-run cache

- `_retrieve_rag_context()` in `app/graph/nodes/enricher.py` filters Pinecone queries by `rag_owner_key` or `user_id` but trusts the returned metadata once Pinecone responds.
- The enricher currently calls `_retrieve_rag_context()` once per raw recipe in `_enrich_single_recipe()`, which creates the roadmap’s N+1 retrieval pattern.
- `app/models/ingestion.py` defines `CookbookChunk.to_pinecone_metadata()` with `user_id`, and `app/ingestion/embedder.py` adds `rag_owner_key`, so both ownership fields are available for assertion on the read path.
- The current graph node is already resilient to empty RAG context, so dropping mismatched-owner chunks should degrade to “less context” rather than pipeline failure.

## Constraints That Matter For Planning

### Rate-limit fix constraints

- The Phase 3 decision is a hybrid policy: authenticated requests keyed by user identity at `10/minute`, unauthenticated fallback keyed by IP at `5/minute`.
- This must preserve current route signatures and response shapes; only the throttle policy and tests should change.
- Because `create_session()` already requires `current_user`, unauthenticated fallback is mainly a defensive key-function path, not a new public endpoint mode.
- Reuse the existing slowapi stack; no new dependency or alternative limiter should be introduced.

### Kitchen validation constraints

- `max_burners` ceiling is fixed at `10`.
- Max equipment count per user is fixed at `20`.
- Invalid relational data must be rejected, never normalized.
- Cross-field invariants must reject `len(burners) > max_burners` and second-oven rack values when `has_second_oven` is false.
- Validation should happen before the scheduler consumes the data, but should remain compatible with current persisted SQLModel usage and route contracts.

### RAG ownership/cache constraints

- Ownership mismatches are dropped and logged server-side; they do not fail enrichment and do not become user-visible pipeline errors.
- The cache must be scoped to a single pipeline run, keyed by session/query context rather than persisted across sessions.
- Cache placement should stay local to the enricher flow so user isolation and invalidation stay simple.
- The full non-integration regression gate should run after the RAG/cache work because that plan closes the phase-wide performance requirement.

## Recommended Phase Split

### Wave 1 - rate limiting and kitchen validation

- Plan `03-01`: tighten `POST /sessions` rate limiting to the chosen hybrid user/IP policy and add route/middleware regressions.
- Plan `03-02`: add kitchen-config bounds and cross-field validation plus equipment-count enforcement with route-level regressions.

These plans touch different production seams and can execute independently.

### Wave 2 - RAG assertion and per-run cache

- Plan `03-03`: assert returned Pinecone chunk ownership, log and drop mismatches, add per-run query caching, and run the full non-integration suite.

This plan should carry the final regression gate because it closes both `SEC-03` and `PERF-02`.

## Critical Pitfalls

1. **Changing limiter library shape instead of keying**
   - The security gap is mostly policy and key-function wiring, not absence of slowapi itself.

2. **Encoding equipment-count limits in the wrong layer**
   - `Equipment` is not embedded inside `KitchenConfig`, so the per-user count cap likely belongs in the route/service path, with model validators still owning burner/oven invariants.

3. **Normalizing invalid kitchen data**
   - The locked decision is explicit rejection. Any “clip to max” or “drop extras” behavior is a Phase 3 violation.

4. **Trusting Pinecone filters as sufficient**
   - Returned metadata still needs ownership assertion because the phase goal explicitly hardens the read path, not just the query filter.

5. **Cross-session cache leakage**
   - A user-shared or process-global cache would create unnecessary invalidation and isolation risk; keep cache lifetime inside one pipeline run.

## Validation Architecture

### Quick checks

- `pytest -q tests/test_api_routes.py tests/test_middleware.py -o addopts='' -k 'create_session or rate limit or kitchen'`
- `pytest -q tests/test_enricher_integration.py -o addopts='' -k 'rag or context or owner or cache'`

### Full gate

- `pytest -m "not integration" -v`

### Sampling guidance

- Run the session rate-limit targeted check after each task in Plan `03-01`.
- Run the kitchen validation targeted check after each task in Plan `03-02`.
- Run the enricher targeted check after each task in Plan `03-03`, then the full non-integration suite before phase verification.

## Planning Implications

- Phase 3 should stay entirely backend-focused: routes, SQLModel/Pydantic validation, enricher behavior, and tests.
- The final RAG/cache plan should be the only plan that owns the full-suite regression gate.
- Because security enforcement is enabled in project config, each execute plan should include a `<threat_model>` section with explicit STRIDE references.
- Plan docs should call out the exact file seams already identified in the context so execution agents do not expand scope into unrelated auth or frontend work.
