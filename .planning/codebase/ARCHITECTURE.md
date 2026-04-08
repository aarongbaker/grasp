# Architecture

**Analysis Date:** 2026-04-08

## Pattern Overview

**Overall:** LangGraph-based linear state machine with conditional error routing and checkpoint persistence.

**Key Characteristics:**
- **Streaming execution model** — TypedDict-based state passed between 6 sequential nodes plus 3 terminal nodes
- **Operator-based accumulators** — `errors` and `token_usage` fields use `operator.add` for accumulation across nodes; all other fields use replace semantics
- **Checkpoint-driven fault tolerance** — State persisted to PostgreSQL after each node; resume capability via LangGraph
- **Per-recipe error isolation** — Enricher, validator, and DAG builder apply recoverable errors per recipe; pipeline continues with survivors
- **Deterministic scheduling** — DAG merger uses greedy list scheduling with resource conflict detection; no backtracking or randomisation
- **Hybrid LLM pattern** — Some nodes call Claude (generator, enricher, renderer); others are pure algorithms (validator, DAG builder, merger)

## Layers

**Input & Routing Layer:**
- **Purpose:** Accept dinner concepts, resolve library selections, manage session lifecycle
- **Location:** `app/api/routes/` (sessions.py, users.py, authored_recipes.py) + `app/workers/`
- **Contains:** REST endpoints, Celery task wrappers, user profile management
- **Depends on:** Database models, pipeline state builders
- **Used by:** Frontend HTTP clients, Celery workers

**State Management Layer:**
- **Purpose:** Define canonical state schema and validation rules
- **Location:** `app/models/pipeline.py`, `app/models/recipe.py`, `app/models/scheduling.py`, `app/models/errors.py`
- **Contains:** TypedDict schemas (GRASPState), domain models (RawRecipe, EnrichedRecipe, ValidatedRecipe, RecipeDAG, ScheduledStep), error types
- **Depends on:** Pydantic, Python typing
- **Used by:** All nodes and route handlers

**LangGraph Execution Layer:**
- **Purpose:** Orchestrate 6 processing nodes + 3 terminal nodes with conditional routing and checkpointing
- **Location:** `app/graph/graph.py` (topology), `app/graph/router.py` (routing logic)
- **Contains:** StateGraph topology, error_router (fatal vs continue), final_router (complete vs partial)
- **Depends on:** LangGraph, PostgreSQL checkpointer, all node implementations
- **Used by:** Celery tasks and route handlers that need status_projection()

**Node Layer (Core Processing):**
- **Purpose:** Implement 6 sequential processing steps
- **Location:** `app/graph/nodes/`
- **Nodes:**
  1. **generator.py** — Generate or retrieve initial RawRecipe objects from concept
  2. **enricher.py** — Add timing, resources, dependencies via Claude + Pinecone RAG
  3. **validator.py** — Pydantic validation and warns; per-recipe recoverable
  4. **dag_builder.py** — Extract dependencies, build NetworkX DAGs per recipe, detect cycles
  5. **dag_merger.py** — Merge DAGs, apply resource constraints, greedy list schedule
  6. **renderer.py** — Convert ScheduledStep objects to timeline + call Claude for summary
- **Depends on:** State models, LLM clients, NetworkX, Pydantic
- **Used by:** LangGraph graph execution

**Persistence & Observability Layer:**
- **Purpose:** Manage session state, derive status projections, record token usage
- **Location:** `app/core/status.py`, `app/db/session.py`, `app/core/logging.py`
- **Contains:** finalise_session() (writes terminal state), status_projection() (derives in-progress status), token usage tracking
- **Depends on:** SQLAlchemy/SQLModel, LangGraph checkpoint reader
- **Used by:** Route handlers and Celery task wrappers

**External Integration Layer:**
- **Purpose:** Manage Claude LLM calls, Pinecone RAG, database connections
- **Location:** `app/core/llm.py`, `app/core/settings.py`, `app/db/session.py`
- **Contains:** LLM retry logic, token extraction, Pinecone client initialization, settings validation
- **Depends on:** LangChain, Pinecone SDK, environment variables
- **Used by:** Nodes and startup hooks

## Data Flow

**Session Lifecycle (Request → Completion):**

1. **Session Creation** (`POST /api/v1/sessions`)
   - Route: `app/api/routes/sessions.py::create_session()`
   - Validates DinnerConcept (free-text or library selection)
   - Creates Session row with status = GENERATING
   - Enqueues Celery task: `grasp.run_pipeline(session_id, user_id)`
   - Returns session_id immediately (async)

2. **Pipeline Execution** (Celery worker)
   - Task: `app/workers/tasks.py::run_grasp_pipeline()`
   - Constructs initial GRASPState via `build_session_initial_state()`
   - Calls `graph.ainvoke(initial_state)` to execute all 6 nodes
   - On completion (success or failure), calls `finalise_session()` exactly once
   - Terminal state persisted to Session row

3. **Status Polling** (`GET /api/v1/sessions/{id}`)
   - Route: `app/api/routes/sessions.py::get_session()`
   - If session.status is terminal (COMPLETE, FAILED, PARTIAL) → return cached result from Session row
   - Else → call `status_projection(session_id, graph)` from checkpoint
   - Projection derives status from which fields are populated in live GRASPState

**Node-to-Node Data Flow (Generator → Renderer):**

```
START
  ↓
[recipe_generator]  → raw_recipes populated
  ↓ [error_router: fatal? → handle_fatal_error, else continue]
[rag_enricher]      → enriched_recipes populated (with timing/deps)
  ↓ [error_router]
[validator]         → validated_recipes populated (warnings added)
  ↓ [error_router]
[dag_builder]       → recipe_dags populated (NetworkX DAGs)
  ↓ [error_router]
[dag_merger]        → merged_dag + schedule populated
  ↓ [error_router: after merger, no fatal routing]
[schedule_renderer] → schedule.summary + error_summary populated
  ↓ [final_router: complete? → mark_complete, else mark_partial]
[mark_complete or mark_partial]
  ↓
END
```

**Error Accumulation & Recovery:**

- Each node appends NodeError objects to state.errors via operator.add reducer
- error_router checks last error: if recoverable=False → fatal path (halts at handle_fatal_error)
- Per-recipe errors (enricher, validator, dag_builder) are recoverable; failed recipes dropped, survivors continue
- If ALL recipes of a course fail → fatal error (no valid menu possible)
- final_router: if any errors exist (even recoverable) → PARTIAL status; else COMPLETE

## Key Abstractions

**GRASPState (TypedDict):**
- **Purpose:** Central state machine state, serialisable to JSON for checkpoint persistence
- **Fields:**
  - concept (DinnerConcept.model_dump()) — user input and meal metadata
  - kitchen_config + equipment — user's kitchen snapshot
  - raw_recipes, enriched_recipes, validated_recipes — recipe progression
  - recipe_dags — per-recipe dependency graphs (NetworkX representation as edge lists)
  - merged_dag + schedule — final timeline and natural-language summary
  - errors, token_usage — accumulators (operator.add reducers)
- **Pattern:** All Pydantic models stored as dicts; nodes call model_validate() at boundaries to get typed instances
- **Checkpoint:** Serialised after each node; resumable on failure

**RecipeDAG (Pydantic model):**
- **Purpose:** Per-recipe dependency graph representation
- **Fields:** recipe_name, recipe_slug, steps (list[RecipeStep]), edges (list[tuple[str, str]])
- **Implementation:** Edges stored as adjacency list (JSON-safe); converted to NetworkX DiGraph at runtime
- **Used by:** DAG merger for topological sort and resource conflict detection

**ScheduledStep (Pydantic model):**
- **Purpose:** RecipeStep with absolute timing resolved by DAG merger
- **Fields:** step_id, recipe_name, start_at_minute, end_at_minute, resource, duration_minutes, merged_from (for consolidated steps), allocation
- **Sorting:** Output sorted by (start_at_minute, recipe_name, step_id) for determinism
- **Burner tracking:** Optional burner_id/burner_position/burner_label for stovetop heat conflict resolution

**NodeError (Pydantic model):**
- **Purpose:** Typed error representation, distinguishes recoverable vs fatal
- **Fields:** node_name, error_type (VALIDATION_FAILURE, RESOURCE_CONFLICT, LLM_FAILURE, etc.), message, recoverable, source_recipe_id
- **Pattern:** Enricher/validator/dag_builder errors are per-recipe recoverable; generator/dag_merger/renderer can be fatal
- **Accumulation:** Appended to state.errors via operator.add; terminal state includes all accumulated errors

## Entry Points

**HTTP API Entry Points:**

1. **Session Creation** — `POST /api/v1/sessions`
   - Location: `app/api/routes/sessions.py::create_session()`
   - Triggers: Celery task enqueue
   - Responsibilities: Validate DinnerConcept, create Session row, return session_id

2. **Session Status** — `GET /api/v1/sessions/{id}`
   - Location: `app/api/routes/sessions.py::get_session()`
   - Triggers: No state change; read-only
   - Responsibilities: Two-tier read (cached vs live), return SessionStatus

3. **Pipeline Run** — `POST /api/v1/sessions/{id}/run`
   - Location: `app/api/routes/sessions.py::run_session()`
   - Triggers: Manual pipeline enqueue (used in tests; normally auto-enqueued)
   - Responsibilities: Set status to GENERATING, enqueue task

**Celery Task Entry Point:**

- **Task name:** `grasp.run_pipeline(session_id, user_id)`
- **Location:** `app/workers/tasks.py::run_grasp_pipeline()`
- **Triggers:** Session creation, manual retry
- **Responsibilities:** Load session/user/kitchen/equipment, build initial state, call graph.ainvoke(), finalise_session()

**Application Startup:**

- **Hook:** `app/main.py::lifespan()` asynccontextmanager
- **Responsibilities:** Validate JWT secret, check CORS origins, initialise Pinecone client, create LangGraph checkpoint tables
- **Graph compilation:** Lazy (on first request or Celery task) to avoid startup blocking

## Error Handling

**Strategy:** Layered recovery with fatal/recoverable distinction

**Patterns:**

1. **LLM Failures (generator, enricher, renderer):**
   - `llm_retry()` wrapper with exponential backoff (up to 3 attempts)
   - Timeout detection: if error message contains "timeout" or "deadline_exceeded" → is_timeout_error()
   - If all retries exhausted:
     - Generator: fatal (no recipes → no menu)
     - Enricher: per-recipe recoverable (drop failed recipe, continue with others)
     - Renderer: recoverable (fallback to basic summary, schedule still returned)

2. **Validation Failures (validator node):**
   - Pydantic validation errors → per-recipe recoverable warnings
   - Recipe dropped from pipeline but reported in error_summary

3. **Cycle Detection (dag_builder):**
   - NetworkX.is_directed_acyclic_graph() check
   - If cycle found: per-recipe fatal (ValueError caught, treated as per-recipe recoverable)
   - All cyclic recipes dropped

4. **Resource Conflicts (dag_merger):**
   - Detected at merge time: overlapping oven temps, stovetop heat saturation, hands/equipment contention
   - Classification: compatible, resequence_required, irreconcilable
   - If irreconcilable with only one oven → fatal (no valid schedule possible)
   - Resequence hints returned in OneOvenConflictRemediation for future reruns

5. **Checkpoint Resume (LangGraph):**
   - If node fails mid-execution: PostgreSQL checkpoint contains state up to previous node's completion
   - Next invocation (same thread_id) resumes from checkpoint, skipping completed nodes
   - SIMULATE_INTERRUPT env var (test only): dag_builder can be forced to fail for resume testing

## Cross-Cutting Concerns

**Logging:** 
- Setup: `app/core/logging.py::setup_logging()` called at app startup
- Per-node logging via `logger = logging.getLogger(__name__)`
- Key events logged: node start/end, error accumulation, LLM calls, resource conflicts

**Validation:**
- Pydantic models validate at boundaries: input (CreateSessionRequest) and state transitions (model_validate on resume)
- DinnerConcept validates concept_source contracts (cookbook_selected vs free_text constraints)
- EnrichedRecipe validates depends_on references exist within the recipe
- RecipeDAG validates acyclicity via NetworkX

**Authentication:**
- JWT bearer token in Authorization header → app/core/auth.py
- Route dependency: CurrentUser extracted via Depends(get_current_user)
- Session ownership checked: user_id from token must match session.user_id

**Token Usage Tracking:**
- Each LLM node extracts tokens via extract_token_usage(response.usage) 
- Tokens appended to state.token_usage (operator.add accumulator)
- Terminal state includes per_node breakdown in finalise_session()

**Resource Constraints (Scheduling):**
- Resource pools: HANDS(1), STOVETOP(max_burners), OVEN(1-2), PASSIVE(infinite)
- Conflicts: overlapping non-PASSIVE resources with same equipment
- Oven temperature tolerance: 15°F (configurable, default 15)
- Burner assignment: explicit allocation when DAG merger detects stovetop contention
