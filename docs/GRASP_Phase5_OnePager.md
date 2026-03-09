# GRASP — Phase 5: RAG Enricher & Validator Nodes

**STATUS:** COMPLETE — Phase 6 next

---

## Overview

Phase 5 replaces `graph/nodes/mock_enricher.py` with a real Claude + Pinecone RAG enricher, and promotes `mock_validator.py` to `validator.py` (the mock already runs real Pydantic validation — the code is identical). The graph topology from Phase 3 is untouched — two import lines swap, two files are created, two mocks are deleted. Phase 3 regression tests remain green via LLM + RAG mocking.

---

## What Changed

| Action | File | Detail |
|--------|------|--------|
| CREATE | `graph/nodes/enricher.py` | Real RAG enricher node — core deliverable |
| CREATE | `graph/nodes/validator.py` | Promoted from mock (identical logic) |
| MODIFY | `graph/graph.py:29-30` | Swap imports: `mock_enricher` → `enricher`, `mock_validator` → `validator` |
| DELETE | `graph/nodes/mock_enricher.py` | Replaced by real node |
| DELETE | `graph/nodes/mock_validator.py` | Replaced by real node |
| MODIFY | `models/pipeline.py:65` | Add `user_id: str` to `GRASPState` |
| MODIFY | `workers/tasks.py:74` | Add `user_id` to initial state dict |
| MODIFY | `tests/conftest.py` | Add enricher mock fixtures (LLM + RAG) + `user_id` to base state |
| MODIFY | `tests/test_phase3.py:147` | Run 2 uses `enricher_fail_fondant` fixture (replaces test_mode mechanism) |
| CREATE | `tests/test_enricher_integration.py` | 9 unit tests + 1 integration test |

---

## Enricher Node Contract

| Component | Detail |
|-----------|--------|
| **Input** | `GRASPState` — reads `raw_recipes` (list of RawRecipe dicts), `user_id` (for Pinecone filtering) |
| **Output** | `{"enriched_recipes": [EnrichedRecipe.model_dump(), ...]}` — list of enriched recipes as dicts |
| **LLM call** | Claude Sonnet via `langchain-anthropic` structured output. One call per recipe. `ChatAnthropic.with_structured_output(StepEnrichmentOutput)` enforces Pydantic schema. |
| **RAG retrieval** | OpenAI `text-embedding-3-small` to embed query → Pinecone vector search filtered by `user_id`. Returns top-K cookbook chunks (techniques, tips, ratios). |
| **RAG fallback** | If RAG returns zero results or fails, enrichment proceeds without RAG context (empty `rag_sources`). RAG is supplementary, not required. |
| **Error handling** | Per-recipe recoverable errors. LLM failure on one recipe → drop it, continue. All recipes fail → fatal (`recoverable=False`). |
| **Idempotency** | Returns `enriched_recipes` as a complete replacement list. |

---

## LLM Integration

**Model:** `claude-sonnet-4-20250514` (same as generator)

**Wrapper model** (for structured output):
```
StepEnrichmentOutput
  steps: list[RecipeStep]
  chef_notes: str
  techniques_used: list[str]
```

**Mockable seams:**
- `_create_llm()` — returns `ChatAnthropic` instance. Tests patch to bypass real API.
- `_retrieve_rag_context()` — embeds query + queries Pinecone. Tests patch to return empty list.

---

## RAG Retrieval

**Query construction:** `"{recipe_name} {cuisine} {description}"` — captures the recipe's identity for vector search.

**Pinecone filter:** `{"user_id": {"$eq": user_id}}` — per-chef isolation.

**Top-K:** `settings.rag_retrieval_top_k` (default 5).

**Embedding model:** `text-embedding-3-small` (1536 dims) — same as ingestion pipeline.

**Graceful degradation:** If Pinecone query fails (network, auth, empty index), the enricher proceeds with LLM-only enrichment. `rag_sources` is set to `[]`. This ensures the pipeline never fails due to missing cookbooks.

---

## System Prompt Design

The enrichment prompt has 6 sections per recipe:

1. **Role** — GRASP enrichment specialist that converts raw recipes to structured, schedulable steps
2. **Raw Recipe** — name, description, cuisine, servings, ingredients, flat steps verbatim
3. **Step ID Convention** — `{recipe_slug}_step_{n}` format, slug provided by the system
4. **Resource Types** — OVEN, STOVETOP, PASSIVE, HANDS with exclusivity rules
5. **RAG Context** — retrieved cookbook chunks (if any) to inform timing, techniques, chef notes
6. **Output Requirements** — duration estimates, dependency inference, prep-ahead identification, non-empty descriptions

---

## Step ID Generation

Recipe slug is generated programmatically (not by the LLM):
```python
slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
```

Step IDs follow the format `{slug}_step_{n}` where n is 1-indexed. The slug is passed to the LLM as a constraint to ensure deterministic, consistent IDs.

---

## user_id in State

`user_id` is added to GRASPState for Pinecone RAG filtering. Same pattern as `equipment` in Phase 4:

- **GRASPState** gets new field: `user_id: str` (non-breaking, `total=False`)
- **Celery task** passes `user_id` into initial state
- **Enricher** reads `state.get("user_id", "")` for Pinecone filter
- **Existing checkpoints** resume safely — missing field defaults to `""`

---

## Validator Node

The validator is a direct promotion of `mock_validator.py`. The mock already ran real Pydantic validation:

- Per-recipe `EnrichedRecipe.model_validate()` — triggers field and model validators
- `RecipeStep.duration_minutes > 0`
- `RecipeStep.duration_max >= duration_minutes`
- `depends_on` references all exist in the recipe's step list
- Per-recipe failure = recoverable, all fail = fatal

No logic changes. File rename + import swap only.

---

## Error Handling

| Error Type | Trigger | Recoverable | Result |
|------------|---------|-------------|--------|
| `RAG_FAILURE` | Pinecone query or LLM enrichment fails | Yes (per-recipe) | Drop recipe, continue |
| `LLM_TIMEOUT` | Anthropic API timeout during enrichment | Yes (per-recipe) | Drop recipe, continue |
| All recipes fail | Every recipe errors | No (fatal) | `error_router` → `handle_fatal_error` → END |
| `VALIDATION_FAILURE` | Pydantic validation fails (validator node) | Yes (per-recipe) | Drop recipe, continue |

---

## Test Strategy

### Phase 3 Regression (mocked) — all 5 tests pass
- Patches `graph.nodes.enricher._create_llm` and `graph.nodes.enricher._retrieve_rag_context` at session scope
- Enricher mock LLM inspects `HumanMessage` content to match recipe name → returns corresponding fixture `StepEnrichmentOutput` (short ribs, pommes puree, or fondant)
- Mock RAG returns empty list (no Pinecone dependency in tests)
- Enricher node's own logic (state reading, slug generation, result formatting) still runs for real; only LLM + RAG are stubbed

### Recoverable Error Test (Run 2) — test mechanism change
- **Old (Phase 3-4):** `test_mode="recoverable_error"` checked by `mock_enricher.py`
- **New (Phase 5):** `enricher_fail_fondant` conftest fixture adds `"Chocolate Fondant"` to module-level `_enricher_skip_recipes` set. The enricher mock's `ainvoke` side_effect raises when the message matches a name in this set. Function-scoped cleanup restores default behavior.
- Test assertions unchanged — still verifies RAG_FAILURE error, 2-recipe schedule, PARTIAL status.

### Unit Tests (9 tests, no API calls)
- `test_generate_recipe_slug()` — slug generation from recipe names
- `test_format_rag_context_empty()` — fallback text when no chunks
- `test_format_rag_context_with_chunks()` — chunk formatting with type labels
- `test_build_enrichment_prompt_includes_recipe()` — prompt structure with recipe data
- `test_build_enrichment_prompt_includes_rag_context()` — RAG chunks in prompt
- `test_build_enrichment_prompt_resource_types()` — all 4 resource types explained
- `test_enricher_per_recipe_error_keeps_survivors()` — one recipe fails, others succeed
- `test_enricher_all_fail_is_fatal()` — all recipes fail → fatal error
- `test_enricher_empty_raw_recipes()` — empty input → fatal error

### Integration Test (1 test, real Claude API)
- Marked `@pytest.mark.integration` — excluded from normal runs
- Calls enricher with real concept (Pan-Seared Salmon), RAG mocked to empty
- Validates: proper step_ids, valid resources, positive durations, consistent depends_on, non-empty chef_notes and techniques_used
- Skips gracefully if `ANTHROPIC_API_KEY` not set

### Running tests
```bash
.venv/bin/python -m pytest tests/ -m "not integration" -v    # 55 tests (fast, no API key)
.venv/bin/python -m pytest tests/ -m integration -v          # Real LLM validation (needs API key)
```

---

## Execution Order (completed)

1. ~~Add `user_id` field to `GRASPState` (`models/pipeline.py`)~~
2. ~~Update `workers/tasks.py` to pass `user_id` into initial state~~
3. ~~Create `graph/nodes/enricher.py` (full implementation)~~
4. ~~Create `graph/nodes/validator.py` (copy from mock)~~
5. ~~Swap imports in `graph/graph.py` lines 29-30~~
6. ~~Delete `graph/nodes/mock_enricher.py`~~
7. ~~Delete `graph/nodes/mock_validator.py`~~
8. ~~Update `tests/conftest.py` (mock fixtures + user_id in base state)~~
9. ~~Update `tests/test_phase3.py` (Run 2 uses `enricher_fail_fondant` fixture)~~
10. ~~Create `tests/test_enricher_integration.py`~~
11. ~~Run Phase 3 tests — all 55 green~~

---

## Remaining Mock Nodes (Phases 6-7)

| File | Phase | What it becomes |
|------|-------|-----------------|
| `graph/nodes/mock_dag_builder.py` | 6 | NetworkX per-recipe DAG builder |
| `graph/nodes/mock_dag_merger.py` | 6 | Cross-recipe resource-aware scheduler |
| `graph/nodes/mock_renderer.py` | 7 | Natural language timeline generator |

---

GRASP Phase 5 One-Pager V1.1
