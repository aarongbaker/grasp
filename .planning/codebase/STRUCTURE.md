# Codebase Structure

**Analysis Date:** 2026-04-08

## Directory Layout

```
grasp/
├── app/                              # Core backend application
│   ├── api/
│   │   └── routes/                   # FastAPI route handlers
│   │       ├── sessions.py           # Session creation, status, pipeline enqueue
│   │       ├── users.py              # User profile, kitchen config, equipment
│   │       ├── auth.py               # JWT auth, login/signup
│   │       ├── ingest.py             # Cookbook/recipe ingestion tasks
│   │       ├── authored_recipes.py   # User's authored recipe CRUD
│   │       ├── recipe_cookbooks.py   # Cookbook management
│   │       ├── admin.py              # Admin utilities
│   │       └── health.py             # Liveness checks
│   ├── core/                         # Cross-cutting concerns
│   │   ├── llm.py                    # LLM retry logic, token extraction
│   │   ├── status.py                 # Session status projection, finalisation
│   │   ├── auth.py                   # JWT validation, user extraction
│   │   ├── settings.py               # Environment validation, config
│   │   ├── deps.py                   # FastAPI dependency injection
│   │   └── logging.py                # Logging setup
│   ├── db/
│   │   └── session.py                # SQLAlchemy engine, session factory
│   ├── graph/                        # LangGraph state machine
│   │   ├── graph.py                  # StateGraph topology (LOCKED after Phase 3)
│   │   ├── router.py                 # Conditional routing: error_router, final_router
│   │   └── nodes/
│   │       ├── generator.py          # Phase 4: LLM recipe generation
│   │       ├── enricher.py           # Phase 5: RAG + Claude timing/deps
│   │       ├── validator.py          # Phase 5: Pydantic validation
│   │       ├── dag_builder.py        # Phase 6: NetworkX DAG construction
│   │       ├── dag_merger.py         # Phase 6: Greedy list scheduling + conflict detection
│   │       └── renderer.py           # Phase 7: Timeline + Claude summary
│   ├── ingestion/                    # Cookbook/recipe vector embedding
│   │   ├── state_machine.py          # RAG document processing state machine
│   │   ├── embedder.py               # Pinecone embeddings
│   │   └── rasteriser.py             # Document chunking
│   ├── models/                       # Domain models (Pydantic + TypedDict)
│   │   ├── pipeline.py               # GRASPState (TypedDict), DinnerConcept, CreateSession* requests
│   │   ├── recipe.py                 # RawRecipe, EnrichedRecipe, ValidatedRecipe, RecipeStep
│   │   ├── scheduling.py             # RecipeDAG, MergedDAG, ScheduledStep, NaturalLanguageSchedule
│   │   ├── errors.py                 # NodeError, ErrorType enum
│   │   ├── enums.py                  # MealType, Occasion, Resource, SessionStatus, ErrorType
│   │   ├── user.py                   # UserProfile, KitchenConfig, Equipment, BurnerDescriptor
│   │   ├── session.py                # Session (DB row), SessionStatus
│   │   ├── authored_recipe.py        # AuthoredRecipeRecord, AuthoredRecipeCreate
│   │   └── invite.py                 # Invite management
│   ├── workers/                      # Celery tasks
│   │   ├── tasks.py                  # run_grasp_pipeline() task wrapper
│   │   └── celery_app.py             # Celery configuration
│   ├── utils/                        # Utilities (to be explored)
│   └── main.py                       # FastAPI app, lifespan hook, router registration
├── tests/                            # Test suite
│   ├── conftest.py                   # Pytest fixtures (session-scoped graph, mocks)
│   ├── test_phase3.py                # 5 regression tests (all node types)
│   ├── test_m018_integration.py      # Integration tests for specific milestones
│   ├── test_m017_integration.py      # ...
│   ├── test_api_routes.py            # Route handler tests
│   ├── test_status_projection.py     # Status derivation tests
│   ├── test_auth.py                  # JWT auth tests
│   ├── test_oven_temp_conflict.py    # One-oven temperature conflict detection
│   ├── test_stovetop_heat_conflict.py # Burner saturation tests
│   ├── test_llm_retry.py             # LLM retry + timeout handling
│   ├── test_generator_integration.py # Generator node + real Claude calls
│   ├── test_ingestion_tasks.py       # Pinecone embedding tests
│   ├── test_state_machine.py         # Ingestion state machine
│   ├── test_deploy_readiness.py      # Production deployment checks
│   ├── fixtures/
│   │   ├── recipes.py                # Fixture recipes (3-dish sets, cyclic test data)
│   │   └── schedules.py              # Pre-computed schedule fixtures
│   └── __init__.py
├── frontend/                         # React/TypeScript UI (not mapped in this architecture doc)
├── alembic/                          # Database migrations
├── docs/                             # Generated documentation
├── .planning/                        # GSD planning documents
├── .gsd                              # GSD orchestrator state
├── .env                              # Environment variables (not committed)
├── CLAUDE.md                         # UI design guidelines
├── AGENTS.md                         # Agent automation docs
├── README.md                         # Project overview
├── docker-compose.yml                # Local Postgres + Pinecone setup
├── Dockerfile                        # Container image
├── alembic.ini                       # Migration config
└── pyproject.toml                    # Python dependencies
```

## Directory Purposes

**app/api/routes/**
- **Purpose:** HTTP endpoint handlers for all REST operations
- **Contains:** FastAPI router instances, dependency injection, request/response marshalling
- **Key files:** sessions.py (pipeline enqueue), users.py (profile CRUD), auth.py (JWT)
- **Pattern:** Each file exports a router; main.py includes all routers at app.include_router()

**app/core/**
- **Purpose:** Shared infrastructure for all routes and nodes
- **Contains:** LLM client + retry logic, JWT validation, status projection, settings management
- **Dependency injection:** app/core/deps.py provides CurrentUser, DBSession for route handlers
- **Settings:** Loaded from environment; validation in app/core/settings.py

**app/graph/**
- **Purpose:** LangGraph state machine orchestration
- **Contains:** State topology (graph.py), routing logic (router.py), 6 processing nodes
- **Locked:** graph.py topology set in Phase 3; only node imports change in later phases
- **Compilation:** Called once at startup or first request; cached as module-level _graph

**app/graph/nodes/**
- **Purpose:** Individual pipeline processing steps
- **1. generator.py** — Reads DinnerConcept, calls Claude, returns list[RawRecipe]
- **2. enricher.py** — Reads raw_recipes, calls Claude + Pinecone, returns list[EnrichedRecipe]
- **3. validator.py** — Reads enriched_recipes, validates with Pydantic, returns list[ValidatedRecipe]
- **4. dag_builder.py** — Reads validated_recipes, builds NetworkX DAGs, returns list[RecipeDAG]
- **5. dag_merger.py** — Reads recipe_dags, merges + schedules, returns MergedDAG + NaturalLanguageSchedule
- **6. renderer.py** — Reads merged_dag, calls Claude for summary, returns final NaturalLanguageSchedule
- **Mockable seams:** Each node has _create_llm() (generator, enricher, renderer) or _build_single_dag() (dag_builder) for testing

**app/models/**
- **Purpose:** Domain model definitions (Pydantic + TypedDict)
- **pipeline.py** — GRASPState (TypedDict), DinnerConcept, all CreateSession* request types
- **recipe.py** — RawRecipe → EnrichedRecipe → ValidatedRecipe (composition pattern)
- **scheduling.py** — RecipeDAG, MergedDAG, ScheduledStep, NaturalLanguageSchedule, OneOvenConflictSummary
- **errors.py** — NodeError (recoverable vs fatal), ErrorType enum
- **enums.py** — MealType, Occasion, Resource (HANDS/STOVETOP/OVEN/PASSIVE), SessionStatus, ErrorType
- **user.py** — UserProfile, KitchenConfig, Equipment, BurnerDescriptor (for stovetop heat conflict resolution)
- **session.py** — Session DB row, SessionStatus enum
- **Pattern:** All models in state stored as dicts (JSON-safe); nodes call model_validate() on resume

**app/ingestion/**
- **Purpose:** Cookbook and authored recipe vector embedding for RAG
- **state_machine.py** — Processing pipeline for ingesting cookbook chunks
- **embedder.py** — OpenAI embeddings → Pinecone vector store
- **rasteriser.py** — Document chunking and preprocessing

**app/workers/**
- **Purpose:** Asynchronous task execution (Celery)
- **tasks.py** — run_grasp_pipeline() task; entry point for pipeline execution
- **Pattern:** Creates its own graph + checkpointer per worker process (stateless per invocation)

**app/db/**
- **Purpose:** SQLAlchemy session management
- **session.py** — Async engine factory, session maker for all DB operations

**tests/**
- **Purpose:** Unit + integration test suite
- **conftest.py** — Pytest fixtures: session-scoped graph with mocked LLM nodes, test DB
- **test_phase3.py** — 5 regression tests covering all node types and error paths
- **test_m0XX_integration.py** — Integration tests for specific milestones (M018, M017, M008, etc.)
- **test_api_routes.py** — Route handler tests (CRUD, auth, status)
- **test_oven_temp_conflict.py** — One-oven temperature feasibility detection
- **test_stovetop_heat_conflict.py** — Burner saturation and assignment
- **fixtures/recipes.py** — Pre-built test recipes (3-dish sets, cyclic graphs)
- **fixtures/schedules.py** — Pre-computed schedules for determinism validation

## Key File Locations

**Entry Points:**
- `app/main.py` — FastAPI app, lifespan hook, router registration
- `app/workers/tasks.py::run_grasp_pipeline()` — Celery task entry point
- `app/api/routes/sessions.py::create_session()` — HTTP API entry (session creation)

**Configuration:**
- `app/core/settings.py` — Environment variable validation, config schema
- `app/graph/graph.py` — LangGraph topology (locked after Phase 3)
- `docker-compose.yml` — Local development Postgres + Pinecone setup

**Core Logic:**
- `app/graph/graph.py` — State machine topology + error routing
- `app/core/status.py` — finalise_session(), status_projection() (session state management)
- `app/models/pipeline.py` — GRASPState schema, DinnerConcept validation

**Pipeline Nodes (in execution order):**
- `app/graph/nodes/generator.py` — (LLM) Recipe generation
- `app/graph/nodes/enricher.py` — (LLM + RAG) Timing & dependency enrichment
- `app/graph/nodes/validator.py` — (Pure Pydantic) Validation
- `app/graph/nodes/dag_builder.py` — (Pure algorithm) DAG construction + cycle detection
- `app/graph/nodes/dag_merger.py` — (Pure algorithm) Scheduling + resource conflict detection
- `app/graph/nodes/renderer.py` — (LLM) Timeline + summary generation

**Domain Models (in data flow order):**
- `app/models/recipe.py` — RawRecipe, EnrichedRecipe, ValidatedRecipe, RecipeStep
- `app/models/scheduling.py` — RecipeDAG, ScheduledStep, MergedDAG, NaturalLanguageSchedule
- `app/models/errors.py` — NodeError with recoverable flag
- `app/models/user.py` — UserProfile, KitchenConfig, Equipment, BurnerDescriptor

**Testing:**
- `tests/conftest.py` — Fixture graph with mocked nodes
- `tests/test_phase3.py` — 5 regression tests covering happy path + error scenarios
- `tests/fixtures/recipes.py` — Pre-built test recipes

## Naming Conventions

**Files:**
- Snake_case: `graph.py`, `router.py`, `status.py`, `session.py`
- Module: one concept per file (e.g., `generator.py` contains only generator node + helpers)
- Node files: `{node_name}.py` (generator.py, enricher.py, dag_builder.py)
- Route files: `{domain}.py` (sessions.py, users.py, auth.py)
- Test files: `test_{feature}.py` (test_phase3.py, test_api_routes.py)

**Directories:**
- Plural for collections: `routes/`, `nodes/`, `models/`, `workers/`, `fixtures/`
- Singular for single purpose: `core/`, `db/`, `graph/`, `ingestion/`
- Functional grouping: `api/` (all HTTP), `graph/` (all state machine)

**Types & Functions:**
- Classes: PascalCase (GRASPState, DinnerConcept, RawRecipe, NodeError)
- Functions: snake_case (recipe_generator_node, build_grasp_graph, finalise_session)
- Private functions: leading underscore (_create_llm, _build_single_dag)
- TypedDict fields: snake_case, all lowercase (user_id, raw_recipes, enriched_recipes)
- Enums: UPPERCASE (ErrorType.VALIDATION_FAILURE, SessionStatus.GENERATING)

**Constants:**
- Module-level: RECIPE_COUNT_MAP, RESOURCE_HEADS_UP (all caps, snake_case)
- Environment: JWT_SECRET_KEY, DATABASE_URL (all caps)

## Where to Add New Code

**New Feature (complete pipeline addition):**
- Modify DinnerConcept + validation in `app/models/pipeline.py`
- Add new node in `app/graph/nodes/new_feature.py`
- Import new node in `app/graph/graph.py` (same import swap pattern)
- Add edge in `app/graph/graph.py::build_grasp_graph()`
- Add route in `app/api/routes/sessions.py` or `app/api/routes/new_feature.py`
- Add tests in `tests/test_new_feature.py` using conftest.py fixtures

**New Route/API Endpoint:**
- Create file in `app/api/routes/{domain}.py`
- Define APIRouter with prefix and endpoints
- Register in `app/main.py::app.include_router()`
- Add auth dependencies via `CurrentUser` from `app/core/deps.py`
- Add tests in `tests/test_api_routes.py` or `tests/test_{domain}.py`

**New Domain Model:**
- Create class in `app/models/{domain}.py`
- Use Pydantic BaseModel for validation
- Use TypedDict for state schema fields (pipeline.py only)
- Add field validators as needed
- Test with conftest.py model_validate() calls

**New Scheduled Task (Celery):**
- Define task in `app/workers/tasks.py` with @celery_app.task decorator
- Task must be async-safe (each worker has its own graph + checkpointer)
- Invoke via `run_task.delay(arg1, arg2)` in route handlers
- Test with mocked Celery config in conftest.py

**Utility Function:**
- Add to `app/utils/` if shared across routes/nodes
- Add to node file if specific to that node
- Use snake_case naming, leading underscore for private helpers

## Special Directories

**app/graph/**
- **Purpose:** Locked topology after Phase 3; only node imports change
- **Generated:** No
- **Committed:** Yes
- **Notes:** graph.py structure preserved across all phases; lines 29-32 (imports) are the ONLY lines that change

**tests/fixtures/**
- **Purpose:** Pre-computed test data (recipes, schedules)
- **Generated:** No (hand-authored in Phase 3)
- **Committed:** Yes
- **Notes:** Used by conftest.py for deterministic testing; fixtures verified against algorithm

**alembic/versions/**
- **Purpose:** Database migrations
- **Generated:** Yes (alembic revision commands)
- **Committed:** Yes
- **Notes:** Applied on deploy via alembic upgrade head; never run in app startup

**.planning/codebase/**
- **Purpose:** GSD codebase analysis documents
- **Generated:** Yes (by /gsd:map-codebase)
- **Committed:** Yes
- **Notes:** Consumed by /gsd:plan-phase and /gsd:execute-phase

**frontend/**
- **Purpose:** React/TypeScript UI (separate from Python backend)
- **Generated:** No
- **Committed:** Yes
- **Notes:** Calls backend REST API; not covered in this architecture analysis
