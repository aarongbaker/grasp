# GRASP — Phase 4: Recipe Generator Node

**STATUS:** IN PROGRESS — Post Phase 3

---

## Overview

Phase 4 replaces `graph/nodes/mock_generator.py` with a real Claude-powered recipe generator. This is the first LLM call in the system. The graph topology from Phase 3 is untouched — one import line swaps, one file is created, one mock is deleted. Phase 3 regression tests remain green via LLM mocking.

---

## What Changes

| Action | File | Detail |
|--------|------|--------|
| CREATE | `graph/nodes/generator.py` | Real generator node — core deliverable |
| MODIFY | `graph/graph.py:28` | Swap import: `mock_generator` → `generator` |
| DELETE | `graph/nodes/mock_generator.py` | Replaced by real node |
| MODIFY | `models/pipeline.py` | Add `equipment: list[dict]` to `GRASPState` |
| MODIFY | `workers/tasks.py` | Load Equipment from DB into initial state |
| MODIFY | `tests/conftest.py` | Add LLM mock fixture + equipment to base state |
| CREATE | `tests/test_generator_integration.py` | Real LLM integration test |
| CREATE | `pytest.ini` | Register `integration` marker |

---

## Generator Node Contract

| Component | Detail |
|-----------|--------|
| **Input** | `GRASPState` — reads `concept` (DinnerConcept), `kitchen_config` (KitchenConfig), `equipment` (list of Equipment dicts) |
| **Output** | `{"raw_recipes": [RawRecipe.model_dump(), ...]}` — list of generated recipes as dicts |
| **LLM call** | Claude Sonnet via `langchain-anthropic` structured output. `ChatAnthropic.with_structured_output(RecipeGenerationOutput)` enforces Pydantic schema on response. |
| **Recipe count** | Derived from `(meal_type, occasion)` lookup table. Dinner party dinner = 3, casual lunch = 1, tasting menu dinner = 5, etc. |
| **Error handling** | Generator failure is always fatal (`recoverable=False`). No retries. `LLM_TIMEOUT` or `LLM_PARSE_FAILURE`. Pipeline halts — nothing can be enriched or scheduled without recipes. |
| **Idempotency** | Returns `raw_recipes` as a complete replacement list. If node re-runs on checkpoint resume, state has N recipes, not 2N. |

---

## LLM Integration

**Model:** `claude-sonnet-4-20250514` (hardcoded V1)

**Wrapper model** (for structured output):
```
RecipeGenerationOutput
  recipes: list[RawRecipe]
```

**Mockable seam:** `_create_llm()` helper function returns the `ChatAnthropic` instance. Tests patch this single function to bypass the real API.

---

## System Prompt Design

The system prompt has 7 sections:

1. **Role** — GRASP expert chef assistant identity
2. **Dinner Concept** — User's `free_text` description verbatim
3. **Menu Parameters** — meal_type, occasion, guest_count, derived recipe count
4. **Dietary Restrictions** — merged from UserProfile defaults + session input
5. **Kitchen Constraints** — max_burners, max_oven_racks, has_second_oven
6. **Available Equipment** — equipment names, categories, and `unlocks_techniques` (e.g., "sous vide circulator — unlocks: precise-temperature cooking")
7. **Guidelines** — exact count, scaling for guests, Celsius temps, dietary compliance, kitchen limits, cuisine attribution, realistic timing, equipment-enabled techniques

---

## Recipe Count Derivation

Lookup table keyed by `(MealType, Occasion)`:

| Occasion | Breakfast | Lunch | Dinner | Brunch | Appetizers | Dessert |
|----------|-----------|-------|--------|--------|------------|---------|
| Casual | 1 | 1 | 2 | 2 | 2 | 1 |
| Dinner Party | 2 | 2 | 3 | 3 | 3 | 2 |
| Tasting Menu | 3 | 4 | 5 | 5 | 5 | 3 |
| Meal Prep | 3 | 3 | 4 | 3 | 3 | 3 |

Default fallback: **3**

---

## Equipment in State

Equipment is snapshotted into `GRASPState` at pipeline start (same pattern as `kitchen_config`). A config change mid-run cannot corrupt an in-progress generation.

- **GRASPState** gets new field: `equipment: list[dict]` (non-breaking, `total=False`)
- **Celery task** queries `Equipment` table by `user_id`, calls `.model_dump()` on each row
- **Generator** reads `state.get("equipment", [])` and formats into the system prompt
- **Existing checkpoints** resume safely — missing field defaults to `[]`

---

## Error Handling

Two error types, both fatal (no retries):

| Error Type | Trigger | Result |
|------------|---------|--------|
| `LLM_TIMEOUT` | `anthropic.APITimeoutError` | Fatal NodeError → `error_router` → `handle_fatal_error` → END |
| `LLM_PARSE_FAILURE` | Any other exception (parse error, validation error, unexpected) | Fatal NodeError → `error_router` → `handle_fatal_error` → END |

Both return `{"raw_recipes": [], "errors": [NodeError.model_dump()]}`.

---

## Test Strategy

### Phase 3 Regression (mocked)
- Patch `graph.nodes.generator._create_llm` in the session-scoped `compiled_graph` fixture
- Mock returns the same 3 fixture recipes (`RAW_SHORT_RIBS`, `RAW_POMMES_PUREE`, `RAW_CHOCOLATE_FONDANT`)
- All 5 existing Phase 3 tests pass unchanged — downstream mocks ignore `raw_recipes`
- The generator node's own logic (state reading, prompt building, result formatting) still runs for real; only the LLM call is stubbed

### Integration Test (real LLM)
- Separate file: `tests/test_generator_integration.py`
- Marked `@pytest.mark.integration` — excluded from normal `pytest` runs
- Calls the generator directly with a real concept, validates output against `RawRecipe` schema
- Asserts: correct recipe count, non-empty fields, servings match guest_count, `estimated_total_minutes > 0`
- Skips gracefully if `ANTHROPIC_API_KEY` not set

### Running tests
```bash
pytest tests/ -m "not integration" -v    # Phase 3 suite (fast, no API key needed)
pytest tests/ -m integration -v          # Real LLM validation (needs API key)
```

---

## Execution Order

1. Add `equipment` field to `GRASPState` (`models/pipeline.py`)
2. Create `graph/nodes/generator.py` (full implementation)
3. Swap import in `graph/graph.py` line 28
4. Delete `graph/nodes/mock_generator.py`
5. Update `workers/tasks.py` to load equipment from DB
6. Update `tests/conftest.py` (mock fixture + equipment in base state)
7. Create `pytest.ini` with marker registration
8. Create `tests/test_generator_integration.py`
9. Run Phase 3 tests — verify all green
10. Run integration test — validate real LLM output

---

GRASP Phase 4 One-Pager V1.0
