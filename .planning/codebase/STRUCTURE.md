# Codebase Structure

**Analysis Date:** 2026-03-18

## Directory Layout

```
grasp/
├── main.py                     # FastAPI app entry point with lifespan hook
├── streamlit_app.py            # Dev-only interactive UI
├── ingest_folder.py            # CLI for bulk cookbook ingestion
├── requirements.txt            # Python dependencies
├── ruff.toml                   # Linting config
├── alembic.ini                 # Database migration config
│
├── .env.example                # Template for environment variables
├── .env                        # Local dev environment (not committed)
├── docker-compose.yml          # Postgres + Redis services
│
├── api/                        # FastAPI routes layer
│   └── routes/
│       ├── auth.py             # POST /auth/token, POST /auth/refresh
│       ├── health.py           # GET /health
│       ├── sessions.py         # POST/GET /sessions, POST /sessions/{id}/run
│       ├── users.py            # GET/PUT /users/{id}, POST /users/{id}/kitchen
│       └── ingest.py           # POST /ingest (cookbook upload)
│
├── core/                       # Cross-cutting: auth, settings, logging, status
│   ├── settings.py             # Pydantic-settings from .env (cached @lru_cache)
│   ├── auth.py                 # JWT decode, get_current_user() dependency
│   ├── deps.py                 # FastAPI dependency injection helpers
│   ├── logging.py              # Logging setup (JSON/human-readable)
│   ├── llm.py                  # LLM client factory, token extraction
│   └── status.py               # finalise_session(), status_projection()
│
├── graph/                      # LangGraph state machine pipeline
│   ├── graph.py                # Topology (add_node, add_conditional_edges, compile)
│   ├── router.py               # error_router, final_router functions
│   └── nodes/                  # 7 pipeline nodes
│       ├── generator.py        # Phase 4: Claude recipe generation
│       ├── enricher.py         # Phase 5: RAG retrieval from cookbook embeddings
│       ├── validator.py        # Phase 5: Pydantic validation of recipes
│       ├── dag_builder.py      # Phase 6: Per-recipe dependency graphs
│       ├── dag_merger.py       # Phase 6: Merge DAGs into one timeline
│       └── renderer.py         # Phase 7: Natural language schedule
│
├── models/                     # Domain models and Pydantic schemas
│   ├── pipeline.py             # GRASPState (TypedDict for LangGraph), DinnerConcept
│   ├── recipe.py               # RawRecipe, EnrichedRecipe, ValidatedRecipe, RecipeStep
│   ├── scheduling.py           # RecipeDAG, MergedDAG, NaturalLanguageSchedule
│   ├── session.py              # Session SQLModel (Postgres table)
│   ├── user.py                 # UserProfile, Equipment, KitchenConfig (SQLModel)
│   ├── ingestion.py            # IngestionJob, IngestedChunk (SQLModel)
│   ├── errors.py               # NodeError, ErrorResponse (Pydantic)
│   └── enums.py                # MealType, Occasion, Resource, SessionStatus, etc.
│
├── db/                         # Data persistence layer
│   └── session.py              # Async SQLAlchemy engine, get_session() dependency
│
├── ingestion/                  # Cookbook OCR + chunking + embedding
│   ├── rasteriser.py           # PDF → text (OCR with Tesseract via pytesseract)
│   ├── classifier.py           # Classify document type (COOKBOOK, REFERENCE, etc.)
│   ├── state_machine.py        # 6-state FSM for recipe detection + chunking
│   └── embedder.py             # Chunk → OpenAI embedding → Pinecone
│
├── workers/                    # Async task execution (Celery)
│   ├── celery_app.py           # Celery app instance config
│   └── tasks.py                # run_grasp_pipeline task (wraps graph.ainvoke)
│
├── alembic/                    # Database migrations (Alembic)
│   ├── env.py
│   ├── script.py.mako
│   └── versions/               # Migration files (auto-generated)
│
├── frontend/                   # React + Vite frontend (separate build)
│   ├── src/
│   │   ├── main.tsx            # React entry
│   │   ├── App.tsx             # Router setup
│   │   ├── pages/              # Page components
│   │   │   ├── LandingPage.tsx
│   │   │   ├── LoginPage.tsx
│   │   │   ├── RegisterPage.tsx
│   │   │   ├── DashboardPage.tsx
│   │   │   ├── NewSessionPage.tsx
│   │   │   ├── SessionDetailPage.tsx
│   │   │   ├── IngestPage.tsx
│   │   │   └── ProfilePage.tsx
│   │   ├── components/
│   │   │   ├── layout/         # Shared layout (AppShell, Sidebar)
│   │   │   ├── landing/        # Landing page sections
│   │   │   ├── session/        # Session display (RecipeCard, Timeline)
│   │   │   └── shared/         # Reusable UI (Button, Input, Select, etc.)
│   │   ├── api/                # Axios/fetch client for backend
│   │   ├── context/            # React context (AuthContext)
│   │   ├── hooks/              # Custom hooks
│   │   ├── types/              # TypeScript type definitions
│   │   └── styles/             # Global CSS
│   ├── public/                 # Static assets
│   ├── dist/                   # Built output (generated)
│   └── vite.config.ts          # Vite bundler config
│
├── tests/                      # Test suite (pytest)
│   ├── conftest.py             # Pytest fixtures
│   ├── fixtures/               # Shared test data
│   └── test_*.py               # Unit and integration tests
│
├── docs/                       # Documentation
└── .planning/codebase/         # GSD analysis documents (this file)
    ├── ARCHITECTURE.md
    ├── STRUCTURE.md
    ├── CONVENTIONS.md
    ├── TESTING.md
    ├── STACK.md
    ├── INTEGRATIONS.md
    └── CONCERNS.md
```

## Directory Purposes

**`api/routes/`:**
- Purpose: HTTP REST endpoints for all external requests
- Contains: FastAPI route handlers (GET, POST, PUT)
- Key files:
  - `auth.py`: JWT token creation/refresh
  - `sessions.py`: Session CRUD, pipeline enqueue, status polling
  - `users.py`: User profile, kitchen config management
  - `ingest.py`: Cookbook file upload

**`core/`:**
- Purpose: Shared utilities and cross-cutting concerns
- Contains: Settings, auth, logging, status derivation, dependency injection
- Key files:
  - `settings.py`: Pydantic-Settings loads .env (cached)
  - `auth.py`: JWT validation, get_current_user() dependency
  - `status.py`: status_projection() and finalise_session() functions
  - `llm.py`: Claude/OpenAI client factory

**`graph/`:**
- Purpose: LangGraph state machine pipeline orchestration
- Contains: Graph topology, routing logic, 7 pipeline nodes
- Key files:
  - `graph.py`: StateGraph definition (nodes + edges)
  - `router.py`: error_router and final_router for conditional routing
  - `nodes/generator.py`: Claude recipe generation (entry point of pipeline)
  - `nodes/enricher.py`: RAG retrieval from Pinecone
  - `nodes/dag_builder.py`: Dependency graph construction
  - `nodes/renderer.py`: Natural language schedule (exit point)

**`models/`:**
- Purpose: All domain models and data schemas (Pydantic + SQLModel)
- Contains: State definitions, recipes, scheduling, database tables, errors
- Key files:
  - `pipeline.py`: GRASPState (TypedDict), DinnerConcept (user input)
  - `recipe.py`: RawRecipe, EnrichedRecipe, ValidatedRecipe (audit trail)
  - `scheduling.py`: RecipeDAG, MergedDAG, NaturalLanguageSchedule
  - `session.py`: Session table (Postgres row)
  - `user.py`: UserProfile, KitchenConfig, Equipment (Postgres rows)
  - `enums.py`: All enums (MealType, SessionStatus, Resource, etc.)

**`db/`:**
- Purpose: Database connection management
- Contains: Async SQLAlchemy engine, session factory, Alembic migrations
- Key files:
  - `session.py`: create_async_engine(), get_session() dependency
  - `alembic/versions/`: Migration files (auto-generated)

**`ingestion/`:**
- Purpose: Cookbook PDF → text → chunks → embeddings pipeline
- Contains: OCR, document classification, recipe detection, embedding
- Key files:
  - `rasteriser.py`: PDF → text (pytesseract)
  - `state_machine.py`: 6-state FSM for recipe boundary detection
  - `embedder.py`: Chunk → OpenAI → Pinecone vector store
  - `classifier.py`: Document type detection

**`workers/`:**
- Purpose: Async task execution layer (Celery)
- Contains: Task definitions, Celery app config
- Key files:
  - `celery_app.py`: Celery instance, concurrency config
  - `tasks.py`: run_grasp_pipeline task wrapper

**`frontend/src/`:**
- Purpose: React + Vite frontend (separate build system)
- Contains: Pages, components, API client, context providers
- Key files:
  - `App.tsx`: React Router setup
  - `pages/DashboardPage.tsx`: Session list
  - `pages/NewSessionPage.tsx`: Meal planning form
  - `pages/SessionDetailPage.tsx`: Results display
  - `api/`: Axios client for backend

**`tests/`:**
- Purpose: Unit and integration test suite (pytest)
- Contains: Test files, fixtures, mocks
- Key files:
  - `conftest.py`: Shared pytest fixtures
  - `test_graph.py`: LangGraph node tests
  - `test_routes.py`: API endpoint tests

## Key File Locations

**Entry Points:**
- `main.py`: FastAPI app server (production)
- `streamlit_app.py`: Interactive test UI (development)
- `ingest_folder.py`: CLI for bulk cookbook ingestion
- `workers/tasks.py::run_grasp_pipeline`: Celery task entry point

**Configuration:**
- `.env.example`: Template for local dev setup
- `requirements.txt`: Python dependencies
- `ruff.toml`: Linting rules
- `alembic.ini`: Migration config
- `docker-compose.yml`: Postgres + Redis services

**Core Logic:**
- `graph/graph.py`: LangGraph topology
- `graph/nodes/generator.py`: Recipe generation (first node)
- `graph/nodes/enricher.py`: RAG retrieval (second node)
- `graph/nodes/renderer.py`: Schedule rendering (last node)
- `models/pipeline.py`: GRASPState definition
- `core/status.py`: Status projection logic

**Database & Persistence:**
- `db/session.py`: Async SQLAlchemy engine
- `models/session.py`: Session table
- `models/user.py`: User profile, equipment, kitchen config
- `alembic/versions/`: All migrations

**Authentication & Authorization:**
- `core/auth.py`: JWT validation
- `api/routes/auth.py`: Token endpoints

**Testing:**
- `tests/conftest.py`: Fixtures
- `tests/test_graph.py`: Node tests
- `tests/test_routes.py`: API tests
- `tests/test_ingestion.py`: Ingestion pipeline tests

## Naming Conventions

**Files:**
- Python modules: lowercase with underscores (`generator.py`, `dag_builder.py`)
- Tests: `test_*.py` (pytest discovery)
- Routes: `{resource}.py` (auth.py, sessions.py, users.py)
- Nodes: `{stage}.py` (generator.py, enricher.py, validator.py)

**Directories:**
- API routes: `api/routes/`
- Graph nodes: `graph/nodes/`
- Test fixtures: `tests/fixtures/`
- Database migrations: `alembic/versions/`
- Frontend components: `frontend/src/components/{category}/`
- Frontend pages: `frontend/src/pages/`

**Functions & Variables:**
- Async functions: `async def function_name()`
- Route handlers: `@router.post("/path")` (kebab-case in URLs)
- Node functions: Named `{stage}_node` (recipe_generator_node, rag_enricher_node)
- Router functions: Named `error_router`, `final_router`
- Pydantic models: PascalCase (DinnerConcept, RawRecipe, ValidatedRecipe)
- Enums: PascalCase (MealType, SessionStatus, Resource)
- Type variables: lowercase with underscores (session_id, user_id)

**Database Tables:**
- SQLModel classes: PascalCase with lowercase tablename (Session, __tablename__ = "sessions")
- Foreign keys: `{table}_id` (user_id, session_id, kitchen_config_id)
- Primary keys: `{table}_id` (session_id, user_id)

## Where to Add New Code

**New API Endpoint:**
- Primary code: `api/routes/{resource}.py`
- Auth dependency: Use `CurrentUser` from `core/deps.py`
- Database access: Use `DBSession` from `core/deps.py`
- Response models: Define in same file or add to `models/`
- Tests: `tests/test_routes.py` or new `tests/test_{resource}.py`

**New Pipeline Node:**
- Implementation: `graph/nodes/{stage_name}.py`
- Function signature: `async def {stage_name}_node(state: GRASPState) -> dict`
- Add node to graph: Edit `graph/graph.py` (add_node + add_conditional_edges)
- Error handling: Catch exceptions, append NodeError to errors list, return {"errors": errors}
- Tests: `tests/test_graph.py` or new `tests/test_graph_nodes.py`

**New Model/Schema:**
- Pydantic model: `models/{domain}.py` (e.g., recipe.py, scheduling.py)
- SQLModel (database table): Add to `models/{domain}.py`, import in `db/session.py::create_db_and_tables()`
- Migration: Auto-generate with Alembic: `alembic revision --autogenerate -m "Add new table"`
- Tests: `tests/test_models.py`

**New Utility/Service:**
- Core utilities: `core/{module}.py` (e.g., core/status.py, core/llm.py)
- Shared helpers: Can go in `core/` or create domain-specific module
- No top-level .py files unless they're entry points (main.py, streamlit_app.py, ingest_folder.py)

**Frontend Component:**
- New page: `frontend/src/pages/{PageName}.tsx`
- New component: `frontend/src/components/{category}/{ComponentName}.tsx`
- Context/state: `frontend/src/context/{ContextName}.tsx`
- API client: Update `frontend/src/api/client.ts`
- Types: Add to `frontend/src/types/api.ts`

**Tests:**
- Unit tests: `tests/test_{module}.py`
- Integration tests: Mark with `@pytest.mark.integration`
- Fixtures: Add to `tests/conftest.py` or `tests/fixtures/{domain}.py`
- Mocking: Mock LLMs with `unittest.mock.patch` or custom fixtures

## Special Directories

**`alembic/`:**
- Purpose: Database schema versioning
- Generated: Yes (migrations auto-generated by `alembic revision --autogenerate`)
- Committed: Yes (all migration files committed)
- Manual steps: Define in `env.py` (already configured), then run `alembic upgrade head` at startup

**`frontend/dist/`:**
- Purpose: Built frontend assets
- Generated: Yes (by `vite build`)
- Committed: No (gitignored)
- Build: Run `npm run build` in frontend/ directory

**`.venv/`:**
- Purpose: Python virtual environment
- Generated: Yes (by `python -m venv .venv`)
- Committed: No (gitignored)
- Activation: `. .venv/bin/activate`

**`__pycache__/` and `.pytest_cache/`:**
- Purpose: Python bytecode and test cache
- Generated: Yes (automatic)
- Committed: No (gitignored)
- Cleanup: Safe to delete anytime

**`.planning/codebase/`:**
- Purpose: GSD analysis documents (ARCHITECTURE.md, STRUCTURE.md, etc.)
- Generated: Yes (by /gsd:map-codebase command)
- Committed: Yes (part of project documentation)

---

*Structure analysis: 2026-03-18*
