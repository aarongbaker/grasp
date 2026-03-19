# Architecture

**Analysis Date:** 2026-03-18

## Pattern Overview

**Overall:** Composable State Machine Pipeline (LangGraph) with Multi-Tier Persistence

GRASP is a **LangGraph-based async state machine** orchestrating a meal-planning pipeline. The system is built as two primary tiers:

1. **FastAPI HTTP tier** — REST API routes, session management, status queries, user auth
2. **LangGraph execution tier** — Async state machine nodes with PostgreSQL checkpointing for resumability

The architecture prioritizes **idempotency** (nodes return complete replacement dicts, never append), **error accumulation** (errors flow through state), and **checkpoint-driven resumability** (pipelines survive worker crashes).

**Key Characteristics:**
- **State machine topology**: Linear pipeline with conditional error routing to fatal handlers or recovery paths
- **Checkpoint persistence**: All intermediate state lives in PostgreSQL (LangGraph checkpointer), not memory
- **Event-driven task enqueueing**: FastAPI routes enqueue Celery tasks; workers execute graph.ainvoke()
- **Composed domain models**: RawRecipe → EnrichedRecipe → ValidatedRecipe (preserves full audit trail)
- **Single-source-of-truth for session status**: Terminal statuses written to DB; in-progress statuses derived from checkpoint on demand

## Layers

**HTTP/REST Layer:**
- Purpose: Accept user requests, manage authentication, enqueue work, return responses
- Location: `api/routes/`
- Contains: FastAPI route handlers for sessions, users, auth, health checks, ingestion
- Depends on: `core/`, `models/`, `db/session`
- Used by: Browser, frontend client, API consumers

**LLM Orchestration Layer:**
- Purpose: Manage the 7-node pipeline state machine; route errors; execute nodes in order
- Location: `graph/`
- Contains: `graph.py` (topology), `router.py` (conditional edges), `nodes/` (individual node implementations)
- Depends on: `models/` (state definitions), `core/llm.py` (LLM client factory)
- Used by: Celery task workers, tests

**Domain Layer (Pipeline State Models):**
- Purpose: Define the typed state object and domain models (recipes, schedules, errors)
- Location: `models/`
- Contains: Pydantic models for recipes, sessions, users; TypedDict for GRASPState
- Depends on: Pydantic, enum definitions
- Used by: All layers (routes, nodes, workers)

**Data Access & Persistence Layer:**
- Purpose: Manage async database connections, session lifecycle, SQLModel ORM
- Location: `db/`, SQLModel table definitions in `models/`
- Contains: `db/session.py` (AsyncSession factory), Alembic migrations in `alembic/`
- Depends on: SQLAlchemy, asyncpg driver
- Used by: Routes, task workers, status projection functions

**Ingestion Layer (Cookbook Processing):**
- Purpose: Process uploaded PDF cookbooks into chunks and embeddings for RAG retrieval
- Location: `ingestion/`
- Contains: Rasterizer (OCR), classifier (document type), state machine (recipe detection), embedder (Pinecone)
- Depends on: Pinecone, OpenAI embeddings
- Used by: `api/routes/ingest.py` and bulk ingest CLI

**Cross-Cutting Concerns:**
- Purpose: Logging, settings, auth, LLM client factory, status projection
- Location: `core/`
- Contains: Settings, JWT auth, logging setup, LLM provider abstraction, status derivation functions
- Depends on: Pydantic-settings, FastAPI, jwt library
- Used by: Every other layer

## Data Flow

**Meal Planning Pipeline (Synchronous from User POV, Async Inside):**

1. **User submits meal concept** (frontend POSTs to `POST /api/v1/sessions`)
   - Request: `CreateSessionRequest` (free_text, guest_count, meal_type, occasion, dietary_restrictions, serving_time)
   - Server: Creates `Session` row with status=PENDING, stores `DinnerConcept` as JSON
   - Response: 201 Created, returns session ID

2. **User triggers pipeline** (`POST /api/v1/sessions/{id}/run`)
   - Route handler: Validates session exists, updates status=GENERATING, enqueues Celery task
   - Returns: 202 Accepted immediately (does NOT wait)
   - Celery worker begins async execution in background

3. **LangGraph pipeline executes** (7 nodes in sequence):
   - **Node 1: recipe_generator** → Calls Claude, returns List[RawRecipe]
   - **[error_router]** → Check last error; fatal → halt, continue → next node
   - **Node 2: rag_enricher** → Pins enriched_recipes from cookbook RAG retrieval
   - **Node 3: validator** → Pydantic validation of recipes
   - **[error_router]** → Same check
   - **Node 4: dag_builder** → Builds per-recipe dependency graphs
   - **Node 5: dag_merger** → Merges individual DAGs into one timeline
   - **[error_router]** → Same check
   - **Node 6: schedule_renderer** → Natural language timeline with step-by-step instructions
   - **[final_router]** → Any errors? partial, no errors? complete
   - **Terminal nodes**: mark_complete or mark_partial

4. **State checkpointing between nodes:**
   - After each node completes, LangGraph writes GRASPState dict to PostgreSQL
   - If worker crashes mid-pipeline, next resume reads from checkpoint (same thread_id)
   - Nodes are idempotent: always return REPLACEMENT state (never append)

5. **Terminal state finalization** (after graph.ainvoke() returns):
   - `finalise_session()` reads terminal GRASPState
   - Writes to Session row: status (COMPLETE/PARTIAL/FAILED), schedule_summary, error_summary, result_recipes, result_schedule
   - Marks completed_at timestamp

6. **Frontend polls for status** (`GET /api/v1/sessions/{id}`):
   - If status is PENDING/GENERATING → call `status_projection()` on checkpoint
   - If status is terminal (COMPLETE/PARTIAL/FAILED) → read Session row directly
   - Returns current SessionStatus, schedule_summary, error count

**Cookbook Ingestion Pipeline:**

1. **User uploads PDF files** (`POST /api/v1/ingest`)
   - Creates `IngestionJob` row, stores file paths

2. **Rasterizer step** (`ingestion/rasteriser.py`)
   - OCRs PDF → raw text

3. **Classifier step** (`ingestion/classifier.py`)
   - Categorizes: COOKBOOK, CULINARY_REFERENCE, GENERAL_KNOWLEDGE

4. **State machine step** (`ingestion/state_machine.py`)
   - Transitions through NARRATIVE → RECIPE_HEADER → INGREDIENTS → METHOD → RECIPE_END
   - Accumulates chunks (recipes kept whole, narrative chunked at ~500 words)

5. **Embedder step** (`ingestion/embedder.py`)
   - Sends chunks to OpenAI embeddings
   - Stores in Pinecone with user_id filter tag (RAG retrieval is per-user)

**State Management:**

`GRASPState` is the single stateful object:

```
GRASPState:
  concept: DinnerConcept (input)
  kitchen_config: KitchenConfig dict (snapshot)
  equipment: list[Equipment] dicts (snapshot)
  user_id: UUID string (for Pinecone filtering)

  [Pipeline stages — populated sequentially]
  raw_recipes: list[RawRecipe] dicts
  enriched_recipes: list[EnrichedRecipe] dicts
  validated_recipes: list[ValidatedRecipe] dicts
  recipe_dags: list[RecipeDAG] dicts
  merged_dag: MergedDAG dict | None
  schedule: NaturalLanguageSchedule dict | None

  [Accumulators — use operator.add as reducer]
  errors: list[NodeError] dicts (ACCUMULATES across nodes)
  token_usage: list[dict] dicts (tracks LLM usage per node)

  test_mode: str | None (Phase 3 only)
```

All fields except `test_mode` are stored as dicts in checkpoint (JSON serialization). Nodes validate using Pydantic at entry/exit.

## Key Abstractions

**LangGraph State Machine:**
- Purpose: Sequential node execution with conditional routing on error state
- Examples: `graph/graph.py` (topology), `graph/router.py` (error_router, final_router)
- Pattern: StateGraph with TypedDict state, conditional_edges for routing, AsyncPostgresSaver for checkpointing

**Error Accumulation & Routing:**
- Purpose: Collect errors across nodes; decide fatal (halt) vs. recoverable (continue)
- Examples: `models/errors.py` (NodeError), `graph/router.py` (error_router checks last error)
- Pattern: All nodes append errors to state.errors list. error_router checks last_error.recoverable to decide path. This timing guarantee works because error_router fires immediately after each node.

**Composed Recipe Models (Audit Trail):**
- Purpose: Preserve all transformations from raw generation → enrichment → validation
- Examples: `RawRecipe → EnrichedRecipe(source: RawRecipe) → ValidatedRecipe(source: EnrichedRecipe)`
- Pattern: Each stage embeds the prior; full lineage available for future diff views

**Status Projection (Read-Optimized Derivation):**
- Purpose: Avoid writing every in-progress status to DB; derive from checkpoint state
- Examples: `core/status.py` (status_projection()`, `api/routes/sessions.py` (GET endpoint)
- Pattern: If schedule populated → shouldn't happen (terminal), elif merged_dag → SCHEDULING, elif validated_recipes → SCHEDULING, elif enriched_recipes → VALIDATING, elif raw_recipes → ENRICHING, else → GENERATING

**Kitchen Configuration Snapshotting:**
- Purpose: Freezes user's kitchen capacity (burners, oven racks) at session start for reproducible scheduling
- Examples: `models/user.py` (KitchenConfig), `workers/tasks.py` (snapshot in initial_state)
- Pattern: Load from user row at task start, store as dict in GRASPState.kitchen_config

## Entry Points

**FastAPI Application:**
- Location: `main.py`
- Triggers: `uvicorn main:app` or `python -m uvicorn main:app`
- Responsibilities:
  - Parse JWT_SECRET_KEY from settings
  - Run Alembic migrations (creates Postgres tables)
  - Initialize Pinecone client
  - Build LangGraph graph with PostgresSaver
  - Register CORS middleware
  - Register rate limiter (Redis or in-memory fallback)
  - Mount routers (auth, health, users, sessions, ingest)
  - Store graph as app.state.graph singleton

**Celery Task Worker:**
- Location: `workers/tasks.py::run_grasp_pipeline`
- Triggers: `celery -A workers.celery_app worker --loglevel=info`
- Responsibilities:
  - Receive (session_id, user_id) from queue
  - Create new event loop, AsyncPostgresSaver, graph instance
  - Load session, user, kitchen config, equipment from DB
  - Build initial GRASPState
  - Call graph.ainvoke(state, config={"configurable": {"thread_id": session_id}})
  - Call finalise_session() with terminal state
  - Dispose of async engine

**Streamlit UI (Development Only):**
- Location: `streamlit_app.py`
- Triggers: `streamlit run streamlit_app.py`
- Responsibilities:
  - Interactive meal planning (form + live preview)
  - Cookbook ingestion (file upload)
  - Development testing (not for production)

**Bulk Ingestion CLI:**
- Location: `ingest_folder.py`
- Triggers: `python ingest_folder.py /path/to/cookbook/pdfs`
- Responsibilities:
  - Create dev user if needed
  - Process all PDFs in directory
  - Print summary of pages/chunks ingested

## Error Handling

**Strategy:** Structured error accumulation with early-exit routing. Errors are first-class state objects.

**Patterns:**

1. **Node-level error capture:**
   ```python
   try:
       result = await llm_call(...)
   except TimeoutError:
       errors.append(NodeError(
           node_name="recipe_generator",
           error_type=ErrorType.LLM_TIMEOUT,
           recoverable=False,  # Timeouts are fatal
           message="Claude took too long",
           metadata={"elapsed_seconds": 65}
       ))
       return {"errors": errors}  # Replace state errors
   ```

2. **Error routing (after every non-terminal node):**
   - If `state.errors[-1].recoverable == False` → route to `handle_fatal_error` → END
   - Else → route to next node in pipeline

3. **Final status decision:**
   - After schedule_renderer: if errors exist (even recoverable) → status=PARTIAL, else → status=COMPLETE
   - If schedule is None → status=FAILED (unrecoverable)

4. **Idempotency on checkpoint resume:**
   - If worker crashes after node 3, next invocation resumes with checkpoint state (has raw + enriched + validated recipes)
   - Nodes are safe to re-run: they always return REPLACEMENT state, never append

## Cross-Cutting Concerns

**Logging:** `core/logging.py` sets up Python logging with JSON output in production, human-readable in dev. Every node logs entry, exit, errors.

**Validation:** Pydantic models validate at node boundaries (entry: validate dict → model; exit: model → dict). GRASPState uses TypedDict for checkpoint compatibility.

**Authentication:** JWT tokens (Bearer header) or legacy X-User-ID header. `core/auth.py::get_current_user()` is FastAPI dependency injected into every route. Token expiry: 60 min (access), 7 days (refresh).

**Rate Limiting:** Redis-backed (production) or in-memory fallback (dev). Routes limited: 30 reqs/min for session creation, 5 reqs/min for pipeline start.

**LLM Token Tracking:** Every node that calls Claude appends `{node_name, input_tokens, output_tokens}` to `state.token_usage`. `finalise_session()` persists total usage to Session.token_usage for observability.

---

*Architecture analysis: 2026-03-18*
