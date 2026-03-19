# Coding Conventions

**Analysis Date:** 2026-03-18

## Naming Patterns

**Files:**
- TypeScript/React components: PascalCase (e.g., `RecipeCard.tsx`, `AuthContext.tsx`, `DashboardPage.tsx`)
- TypeScript modules: camelCase (e.g., `client.ts`, `sessions.ts`, `usePolling.ts`)
- TypeScript type files: lowercase with module purpose (e.g., `api.ts` in `src/types/`)
- CSS Modules: camelCase with `.module.css` suffix (e.g., `RecipeCard.module.css`, `Pipeline.module.css`)
- Python modules: snake_case (e.g., `auth.py`, `logging.py`, `state_machine.py`)
- Python test files: `test_*.py` (e.g., `test_auth.py`, `test_state_machine.py`)
- Python classes (Pydantic models): PascalCase (e.g., `RawRecipe`, `EnrichedRecipe`, `UserProfile`)
- Python functions: snake_case (e.g., `get_current_user`, `run_state_machine`, `bind_session_context`)

**Functions:**
- **TypeScript**: camelCase for all functions and async functions (e.g., `listSessions()`, `createSession()`, `apiFetch()`)
- **Python**: snake_case with clear verb-noun pattern (e.g., `_build_access_token()`, `get_current_user()`, `run_state_machine()`)
- **Private functions**: Prefix with underscore in both languages (e.g., `_make_token()`, `_decode_jwt()`)

**Variables:**
- **TypeScript**: camelCase for all variable names and constants (e.g., `refreshPromise`, `sessionId`, `isExpanded`)
- **Python**: snake_case for variables and constants; UPPER_SNAKE_CASE for module-level constants
- **React hooks**: camelCase following `use*` convention (e.g., `usePolling`, `useSessionStatus`)

**Types:**
- **TypeScript**: PascalCase for interfaces and types (e.g., `ValidatedRecipe`, `Resource`, `MealType`)
- **Python Pydantic models**: PascalCase (e.g., `TokenRequest`, `TokenResponse`)
- **TypeScript enums/unions**: PascalCase with literal strings (e.g., `SessionStatus`, `Resource`)
- **Record/mapping types**: Use uppercase constants for label maps (e.g., `RESOURCE_LABELS: Record<Resource, string>`)

## Code Style

**Formatting:**
- **Frontend**: Prettier enforced via ESLint. No explicit config file — uses ESLint plugin defaults
- **Backend**: Ruff formatter configured in `ruff.toml` with 120-character line length
- **TypeScript**: Vite + TypeScript strict mode enabled (`"strict": true`)

**Linting:**
- **Frontend**: ESLint with flat config at `frontend/eslint.config.js`
  - Extensions: JS recommended + TypeScript ESLint + React Hooks + React Refresh rules
  - No custom rules — relies on community presets
- **Backend**: Ruff with config at `ruff.toml`
  - Enabled rules: `["E", "F", "W", "I"]` (Error, F-string, Warning, isort)
  - Notable ignores: `E501` (line-too-long handled by formatter), `F401` (unused imports — some are intentional re-exports)
  - Per-file ignores for app initialization files and migrations

## Import Organization

**Order (TypeScript):**
1. External libraries (React, third-party packages)
2. Relative imports from project (`./`, `../`)
3. Type imports (use `type` keyword): `import type { ... }`

**Example:**
```typescript
import { useState } from 'react';
import { ChevronDownIcon } from 'lucide-react';
import { apiFetch } from './client';
import type { ValidatedRecipe } from '../../types/api';
```

**Order (Python):**
1. Standard library
2. Third-party packages (pydantic, fastapi, sqlmodel, langgraph, etc.)
3. Local imports (api, core, db, graph, models, workers, tests)

**Path Aliases:**
- Backend: Uses absolute imports from project root (e.g., `from api.routes.auth import ...`, `from models.recipe import ...`)
- Frontend: Relative paths only (e.g., `import { apiFetch } from './client'`)

## Error Handling

**Patterns:**
- **TypeScript API client** (`frontend/src/api/client.ts`):
  - Custom `ApiError` class extending Error with `status` and `detail` properties
  - Graceful network error messages: `"Network error — could not reach the server"`
  - Timeout handling: 30-second default with custom `AbortController`
  - 401 Auto-retry: Attempts silent token refresh on 401, then re-requests

- **TypeScript components**: Use try-catch only in async handlers; errors surfaced via state (loading, error fields)

- **Python routes** (`api/routes/*.py`):
  - Raise `HTTPException` with explicit status codes (401, 404, 409, etc.)
  - Error detail strings are user-facing (e.g., `"Invalid email or password"`, not technical info)
  - Private helpers (`_build_access_token`, `_decode_jwt`) raise HTTPException on validation failure

- **Python business logic** (core, models):
  - Pydantic validators use `ValueError` with descriptive messages
  - State machine transitions use `CookbookState` enums (no stringly-typed states)
  - LangGraph nodes catch exceptions; error_router routes to recovery handlers

## Logging

**Framework:** Python uses `structlog` via `core/logging.py`

**Patterns:**
- **Production**: JSON structured logging (set via `structlog.processors.JSONRenderer()`)
- **Development**: Pretty-print console output
- **Context binding**: `bind_session_context(session_id)` injects `session_id` into all logs for correlation
- **Third-party loggers**: Explicitly quieted (httpx, httpcore, openai, anthropic set to WARNING)
- **Setup function**: `setup_logging()` must be called once at application startup

**No log level configuration in code** — governed by `settings.log_level` env var

## Comments

**When to Comment:**
- Complex state transitions in LangGraph nodes (e.g., test_phase3.py has extensive docstring comments)
- Non-obvious validation logic (e.g., `depends_on` consistency checks in EnrichedRecipe)
- Architectural decisions or gotchas (e.g., composition over inheritance note in models/recipe.py)
- Multi-step algorithms where intent is not obvious from code

**Avoid:** Obvious comments that restate code (e.g., `x = 1  # set x to 1`)

**Module Docstrings:**
- Every Python module has a docstring at the top explaining purpose and key design decisions
- Example: `api/routes/auth.py` explains token types, `models/recipe.py` explains composition pattern

**Function Docstrings:**
- Used selectively in Python for complex functions (not every function)
- Example: `_build_access_token()` includes return type documentation
- TypeScript: Minimal — code is self-documenting due to strict types

## Function Design

**Size:**
- Python: Prefer functions under 50 lines; longer functions extracted into helpers (e.g., `_make_token` helper)
- TypeScript: React components under 200 lines; pure logic functions under 30 lines

**Parameters:**
- Python: Use `Depends()` for FastAPI route injection (e.g., `db: DBSession`, `authorization: Header`)
- TypeScript: Pass props as single object in React components; use typed function signatures elsewhere
- Explicit over implicit: Pydantic models preferred over `**kwargs`

**Return Values:**
- Python: Return Pydantic models from routes (FastAPI auto-serializes to JSON)
- TypeScript: Return typed objects; async functions return Promise<T>
- Nullable returns: Prefer explicit `Optional[T]` in Python; use `T | null` in TypeScript

## Module Design

**Exports:**
- **Python**: Each module exports a focused set of public functions/classes; private helpers prefixed with `_`
- **TypeScript**: Export named functions and types; default exports avoided (one per-file)
- **Barrel files**: Not used; relative imports preferred for clarity

**Dependencies:**
- Python: Core modules (`core/auth.py`, `core/logging.py`) are leaf nodes with no internal dependencies beyond stdlib + pydantic
- Route modules (`api/routes/auth.py`) depend on core but core does not depend on routes
- TypeScript: Component dependencies flow one direction (no circular imports); API client is shared dependency

**API client** (`frontend/src/api/client.ts`):
- Single source of truth for all fetch logic
- `apiFetch<T>()` generic function wraps network calls, token refresh, error handling
- All other API modules (sessions.ts, users.ts, etc.) import and use `apiFetch()`

---

*Convention analysis: 2026-03-18*
