# Coding Conventions

**Analysis Date:** 2026-04-08

## Naming Patterns

### Files
- **Modules**: lowercase with underscores: `generator.py`, `dag_builder.py`, `mock_validator.py`
- **Package directories**: lowercase with underscores: `app/graph/nodes/`, `app/models/`, `tests/fixtures/`
- **Test files**: `test_<phase_or_feature>.py` pattern (e.g., `test_phase3.py`, `test_phase6_unit.py`, `test_oven_temp_conflict.py`)

### Functions
- **Public functions**: snake_case: `_build_single_dag()`, `_merge_dags()`, `_create_llm()`
- **Private/internal functions**: leading underscore prefix for mockable seams and internal helpers: `_create_llm()`, `_retrieve_rag_context()`, `_build_system_prompt()`
- **Mockable seams**: explicitly named with leading underscore and clearly documented in docstrings as patchable (e.g., `_create_llm` in generator, enricher, renderer)
- **Helpers**: descriptive verb-noun pairs: `_format_dietary_restrictions()`, `_derive_recipe_count()`, `_build_timeline_entry()`

### Variables and Constants
- **Local variables**: snake_case: `raw_recipes`, `enriched_recipes`, `validated_recipes`, `session_id`, `kitchen_config`
- **Step ID constants**: UPPERCASE with semantic naming: `SR_STEP_1`, `PP_STEP_1`, `CF_STEP_5` (defined in fixture files for reuse across tests)
- **Configuration maps**: UPPERCASE dictionaries: `RECIPE_COUNT_MAP`, `ALLOWED_RAG_CHUNK_TYPES`, `DEFAULT_RECIPE_COUNT`
- **Module-level control flags**: leading underscore: `_enricher_skip_recipes`, `_enricher_cyclic_mode`, `_generator_ft_mode` (used in conftest.py for test control)

### Types and Classes
- **Pydantic models**: PascalCase: `DinnerConcept`, `RawRecipe`, `EnrichedRecipe`, `ValidatedRecipe`, `NodeError`, `GRASPState`
- **TypedDict models**: PascalCase: `GRASPState`, `InitialPipelineState`
- **Enum classes**: PascalCase: `MealType`, `Occasion`, `ErrorType`, `Resource`, `SessionStatus`
- **Output wrappers**: PascalCase with "Output" suffix: `RecipeGenerationOutput`, `StepEnrichmentOutput`, `ScheduleSummaryOutput`

## Code Style

### Formatting
- **Tool**: ruff (configured in `ruff.toml`)
- **Line length**: 120 characters
- **Target version**: Python 3.12
- **Ignored rules**: E501 (line too long — handled by formatter), F401 (unused imports — intentional re-exports)

### Import Organization
**Order** (per isort configuration in `ruff.toml`):
1. Standard library imports (`asyncio`, `logging`, `uuid`, etc.)
2. Third-party imports (`pydantic`, `langchain`, `sqlalchemy`, `pytest`, etc.)
3. First-party imports (`app.*`, `tests.*`)

**Path aliases** (defined in `ruff.toml` as `known-first-party`):
- `app` — application code root
- `tests` — test code root

**Example**:
```python
import asyncio
import logging
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.pipeline import GRASPState
```

### Module Documentation
- **Docstring format**: Triple-quoted at module top
- **Content**: Purpose, key design decisions, critical contracts, external seams
- **Pattern**: Name the file, state its purpose, list critical details (not inline comments)

**Example from `models/pipeline.py`**:
```python
"""
models/pipeline.py
GRASPState and DinnerConcept — the central pipeline objects.

CRITICAL LangGraph architecture decision:
GRASPState is a TypedDict (not a Pydantic model) because LangGraph requires
TypedDict for its state schema...
"""
```

## Function and Class Design

### Function Signatures
- **Type hints**: Always included (required by mypy compatibility)
- **Return types**: Explicitly stated (never `-> None` implicit)
- **Parameters**: Named clearly, no single-letter vars except loop counters

**Pattern**:
```python
def _build_single_dag(validated_recipe: ValidatedRecipe) -> RecipeDAG:
    """Build a DAG from a validated recipe."""
    pass

async def recipe_generator_node(state: GRASPState) -> dict:
    """Generate raw recipes from dinner concept."""
    pass
```

### Class Design
- **Composition over inheritance**: All pipeline recipes use composition: `RawRecipe → EnrichedRecipe(source: RawRecipe) → ValidatedRecipe(source: EnrichedRecipe)` (see `models/recipe.py` docstring §2.2)
- **Field validators**: Use `@field_validator` for single-field constraints, `@model_validator(mode="after")` for cross-field validation after all fields populated
- **Pydantic `model_config`**: Set `{"extra": "forbid"}` on request models to reject unknown fields

**Example from `models/pipeline.py`**:
```python
class DinnerConcept(BaseModel):
    free_text: str = Field(max_length=2000)
    guest_count: int = Field(ge=1, le=100)
    
    @field_validator("serving_time")
    @classmethod
    def validate_serving_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("serving_time must be in HH:MM 24-hour format")
        return v
```

### Error Handling

**Strategy**: Explicit error types with metadata for context-specific frontend messaging

**NodeError pattern**:
```python
class NodeError(BaseModel):
    node_name: str              # Which node failed
    error_type: ErrorType       # Enum for frontend routing
    recoverable: bool           # Fatal (False) or continue (True)
    message: str                # User-facing message
    metadata: dict[str, Any]    # Error-specific context
```

**Node error handling pattern**:
```python
# Per-recipe error handling (enricher pattern)
try:
    enriched = await enrich_recipe(recipe)
    result_recipes.append(enriched)
except Exception as e:
    errors.append(
        NodeError(
            node_name="recipe_enricher",
            error_type=ErrorType.RAG_FAILURE,
            recoverable=True,  # One recipe failing is recoverable
            message=f"Could not enrich {recipe.name}: {str(e)}"
        )
    )

# Fatal error pattern (generator failure)
if not raw_recipes:
    errors.append(
        NodeError(
            node_name="recipe_generator",
            error_type=ErrorType.GENERATION_FAILURE,
            recoverable=False,  # No recipes = pipeline halts
            message="Failed to generate recipes"
        )
    )
```

**Timeout handling**: Use `is_timeout_error()` from `app.core.llm` to check if exception is transient, don't catch and suppress broadly

**Pattern**:
```python
from app.core.llm import is_timeout_error, llm_retry

@llm_retry  # Retries on APITimeoutError, APIConnectionError, RateLimitError
async def call_llm():
    try:
        return await llm.ainvoke(...)
    except is_timeout_error as e:
        # Log with context
        logger.warning(f"LLM timeout in {node_name}", error=str(e))
```

## State Management

### GRASPState Pattern
- **TypedDict, not Pydantic**: Required by LangGraph for type schema
- **All models stored as dicts**: Models are serialized to dicts on state assignment, deserialized from dicts when read
- **Deserialization at boundaries**: Always call `Model.model_validate(state["field"])` when reading typed objects

**Critical contract** (`models/pipeline.py` §2.10):
> Nodes return partial dicts that replace their specific fields. Never append to raw_recipes, enriched_recipes, etc. Replace the entire list. This makes every node safe to re-run on checkpoint resume without producing duplicate data.

**Example**:
```python
# Generator node returns NEW list, not appended
state_update = {
    "raw_recipes": [recipe1, recipe2, recipe3],  # Not += or append
}

# Later read from state
raw_recipes_dicts = state.get("raw_recipes", [])
raw_recipes = [RawRecipe.model_validate(r) for r in raw_recipes_dicts]
```

### Error Accumulation
- **GRASPState.errors**: Uses `Annotated[list[dict], operator.add]` as reducer
- **Pattern**: LangGraph's `operator.add` accumulates errors across nodes (vs REPLACE semantics for other fields)
- **Access**: `state.get("errors", [])` returns accumulated list

**From `models/pipeline.py` line 419**:
```python
errors: Annotated[list[dict], operator.add]  # ACCUMULATOR — NodeError.model_dump()
```

## Logging

**Framework**: structlog (configured in `core/logging.py`)

**Setup**: Call `setup_logging()` once at startup in `app/main.py`

**Pattern**:
```python
import logging
import structlog

logger = logging.getLogger(__name__)

# Use structured fields
logger.warning(
    "enrichment_failed",
    recipe_name=recipe.name,
    error_type=ErrorType.RAG_FAILURE.value,
    recoverable=True
)

# Bind session context for correlation
from app.core.logging import bind_session_context, clear_session_context
bind_session_context(str(session_id))
# ... do work ...
clear_session_context()
```

**Third-party suppression** (configured in `setup_logging()`):
- `httpx`, `httpcore`, `openai`, `anthropic`: set to WARNING
- In production: `uvicorn.access` also suppressed to reduce log quotas

## Comments

### When to Comment
- **Design decisions and trade-offs**: Use module-level docstrings (e.g., "Composition over inheritance — preserves audit trail")
- **Non-obvious algorithm logic**: Comment `_compute_critical_paths()` but not `list.append()`
- **Contracts and preconditions**: Must be documented (e.g., "step_ids must be globally unique")

### Format
- **Inline comments**: Minimal; prefer clear naming
- **Block comments**: Use `# ──` dividers in sections for visual separation (common in test files)
- **Docstring style**: Triple-quoted with purpose on first line, details below

**Example from `conftest.py`**:
```python
# ── Enricher mock control ────────────────────────────────────────────────────
# Module-level set of recipe names that the enricher mock should simulate
# failure for. Tests add names via the enricher_fail_fondant fixture.
_enricher_skip_recipes: set[str] = set()
```

## Module Organization

### Typical node structure (e.g., `graph/nodes/generator.py`)
1. **Module docstring**: Purpose, design, error handling, mockable seams
2. **Imports**: Standard, third-party, first-party (in that order)
3. **Logger setup**: `logger = logging.getLogger(__name__)`
4. **Output wrapper classes**: Pydantic models for LLM structured output
5. **Helper functions**: Private utilities (`_format_*`, `_build_*`, `_derive_*`)
6. **Main node function**: Async function matching LangGraph signature
7. **Mockable seams** extracted: `_create_llm()` called via wrapper

### Test file structure (e.g., `tests/test_phase6_unit.py`)
1. **Module docstring**: Test purpose, what's being tested, high-level assertions
2. **Imports**: Include all fixtures and models needed
3. **Helper fixtures**: e.g., `_make_validated()` for wrapping test objects
4. **Constants**: Kitchen config, default values
5. **Test classes**: Grouped by component (`TestBuildSingleDag`, `TestMergeDags`)
6. **Individual test methods**: `test_<scenario>_<expected_outcome>()`

## Idempotency and Checkpoint Resume

**Critical pattern** (from `models/pipeline.py` §2.10 and node docstrings):
- Nodes returning recipe lists must return a **new list**, not mutate state
- This ensures running the same node twice produces N items, not 2N
- `state_update = {"raw_recipes": [new_list]}` not `state["raw_recipes"] += [new_item]`

**Deserialization safety**:
- LangGraph checkpoint restore gives plain dicts, NOT Pydantic objects
- Always validate before use: `Recipe.model_validate(state_dict)`
- Never assume `isinstance(state["raw_recipes"][0], RawRecipe)` — it won't be

---

*Convention analysis: 2026-04-08*
