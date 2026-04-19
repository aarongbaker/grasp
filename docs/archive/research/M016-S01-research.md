# S01 Research — Chef-first authored recipe workspace

## Summary
- S01 primarily owns/supports **R022** (chef-friendly authored recipe workflow) and **R030** (intuitive non-technical UX with progressive disclosure), while setting the UX seam that later slices will use for **R023/R024/R025/R026/R027/R028/R029/R031**.
- The active product surface is still intentionally **menu-intent only**. `frontend/src/pages/NewSessionPage.tsx` and its tests explicitly assert there is **no cookbook mode switcher**. Any S01 work must preserve that menu-intent path while introducing a separate authored-recipe workspace in chef language rather than reviving the removed cookbook upload/browse flow.
- There is **no authored-recipe persistence or DB model yet**. Current persisted recipe-adjacent models are either pipeline/checkpoint models (`app/models/recipe.py`, `app/models/pipeline.py`, `app/models/scheduling.py`) or de-scoped internal/admin cookbook-ingestion tables (`app/models/ingestion.py`).
- The natural seam for S01 is therefore **frontend-first**: add a new authenticated route/workspace and vocabulary system that feels kitchen-native, while avoiding any contract changes to `/sessions` or session status ownership in this slice.

## Skills Discovered
- Already relevant and installed in prompt context:
  - `frontend-design` — directly relevant; use for route/page/component design in the warm editorial system.
  - `react-best-practices` — already available; useful if the planner wants performance/state guidance for new React surfaces.
- External skill search run:
  - `React` → found strong matches, but an equivalent installed skill (`react-best-practices`) already exists, so no install needed.
  - `FastAPI` / `SQLModel` → results exist but S01 does not currently need new backend-framework-specific skill guidance because this slice is mostly product-surface research and route/UX shaping.
- No new skills installed.

## Recommendation
Build S01 as a **new authored-recipe workspace entry path** under the authenticated app shell, not as a mutation of the existing menu-intent form into a multi-mode schema surface.

Recommended shape:
1. Preserve `/sessions/new` as the current menu-intent path for generated sessions.
2. Add a new authenticated route/page for authoring discovery/workspace (for example adjacent to session creation, not inside ingestion/admin seams).
3. Keep S01 focused on:
   - pathway framing,
   - chef-readable labels/copy,
   - progressive disclosure scaffolding,
   - kitchen-native sectioning,
   - placeholders/state boundaries that later slices can wire into native structured authoring.
4. Do **not** introduce raw `RecipeStep`, `depends_on`, `resource`, or scheduler jargon directly in the UI. D044 is explicit: optimize for non-technical chefs and do not ship a raw schema editor.
5. Do **not** repurpose cookbook upload/browse models or `/ingest` routes for the authored-recipe workspace. Those are explicitly internal/admin-only after M015.

## Implementation Landscape

### Current user-facing entry flow
- `frontend/src/App.tsx`
  - Authenticated routes currently include only `/`, `/sessions/new`, `/sessions/:sessionId`, `/profile`.
  - S01 can add a new route cleanly without disturbing existing session routes.
- `frontend/src/pages/NewSessionPage.tsx`
  - Current page is a single free-text menu-intent form.
  - Local state only; no extracted pathway chooser or wizard scaffolding.
  - Submission contract is tightly coupled to `createSession(buildRequest())` then `runPipeline(session.session_id)`.
  - Good reference for error/loading handling, but **not** the right place to force authoring UI if that would degrade the menu-intent-first product identity.
- `frontend/src/pages/__tests__/NewSessionPage.test.tsx`
  - Important guardrail: test explicitly verifies the page renders **only menu intent form with no cookbook mode switcher**.
  - If S01 changes `/sessions/new`, planner must either preserve these assertions or intentionally replace them with a clearer authored-pathway contract. Safer default: keep this page intact and add a separate authored workspace route/test file.

### Current shell and navigation seams
- `frontend/src/components/layout/AppShell.tsx`
  - Authenticated wrapper with `Sidebar` + `<Outlet />`.
  - Natural place to expose new workspace navigation without touching auth/session internals.
- `frontend/src/pages/DashboardPage.tsx`
  - Dashboard CTA currently links only to `/sessions/new` with label “Plan a Dinner”.
  - S01 may need a second CTA or a reframed creation area if authored recipes should be discoverable from the dashboard.

### Shared UI primitives available now
- `frontend/src/components/shared/Button.tsx`
- `frontend/src/components/shared/Input.tsx`
- `frontend/src/components/shared/Select.tsx`
- Thin, reusable, CSS-Module-based primitives already match repo conventions.
- No existing tabs/stepper/panel/wizard primitives were found; S01 will likely need new authoring-specific components if the workspace is segmented.

### Existing design constraints
- `CLAUDE.md`
  - Mandatory for frontend work per project knowledge K003.
  - Warm editorial aesthetic; serif headings; humanist sans body; copper reserved for primary actions.
  - Progressive disclosure is explicitly encouraged.
- Project knowledge:
  - K001: CSS Modules only.
  - K002: never use Inter/Roboto/Arial/system stack as design choice.
  - D044: chef-language, intuitive guidance, progressive disclosure; no raw schema editor.

### Existing session and status contract to avoid disturbing in S01
- `app/api/routes/sessions.py`
  - `POST /sessions/{id}/run` is the **only** direct writer of `GENERATING`.
  - Terminal statuses are finalized elsewhere; in-progress statuses are checkpoint-derived.
  - S01 should avoid touching this route unless absolutely necessary.
- `app/models/session.py`
  - `Session.concept_json` stores the persisted `DinnerConcept` JSON.
  - This is session input storage, not a general recipe library seam.
- `app/models/pipeline.py`
  - `DinnerConcept` currently supports `concept_source: "free_text" | "cookbook"` and `selected_recipes`.
  - This is a leftover/legacy bridge for cookbook-selected scheduling input, not a chef-authored recipe domain model.
  - It is useful evidence that the codebase already tolerates multiple session-input pathways, but S01 should not force authored recipes into the cookbook-selection shape.

### Existing recipe-domain models relevant to later slices
- `app/models/recipe.py`
  - Strong native structured recipe model exists: `Ingredient`, `RecipeStep`, `RawRecipe`, `EnrichedRecipe`, `ValidatedRecipe`.
  - Important language mismatch: model fields are scheduler-native and technical (`depends_on`, `resource`, `required_equipment`, `can_be_done_ahead`, `prep_ahead_window`).
  - S01 should treat these as downstream compiled targets, not direct UX labels.
- `app/models/scheduling.py`
  - Confirms what later authoring must eventually satisfy: timing/resource/equipment/prep-ahead semantics are real scheduling atoms.
  - Reinforces the need for progressive disclosure: the model is rich, but direct exposure would violate D044/R030.

### Cookbook/library surfaces that should NOT be reused as the user-facing authored workspace
- `app/api/routes/ingest.py`
  - Header comment explicitly states this is internal/admin-only after M015.
  - User-facing cookbook upload/browse/selection were removed.
- `app/models/ingestion.py`
  - `BookRecord`, `CookbookChunk`, `IngestionJob` are ingestion infrastructure for curated text/Pinecone, not a private authored library UX model.
- `app/graph/nodes/generator.py` + tests in `tests/test_phase7_unit.py` / `tests/test_status_projection.py`
  - Cookbook-mode sessions still exist as a deterministic generator seed path, but that path is built around selected cookbook chunks and parser heuristics.
  - That is not the same thing as chef-authored recipe composition.

### Session presentation seams likely affected later, not necessarily in S01
- `frontend/src/components/session/sessionConceptDisplay.ts`
  - Currently only derives a title from `concept.free_text`.
  - If authored sessions later become first-class session types, this helper is the existing place to centralize authored-vs-generated presentation.
- `frontend/src/components/session/SessionCard.tsx`
- `frontend/src/pages/SessionDetailPage.tsx`
  - These currently assume menu-intent-derived display text and result presentation.
  - S01 probably does not need to change them unless the new workspace creates draft/session artifacts immediately.

## Natural Seams for Task Decomposition
1. **Route and discoverability seam**
   - Files: `frontend/src/App.tsx`, `frontend/src/pages/DashboardPage.tsx`, possibly sidebar/app-shell navigation files.
   - Goal: make the authored workspace reachable without regressing the main menu-intent path.
2. **Authoring workspace page shell**
   - New page file + CSS Module.
   - Chef-language intro, calm hierarchy, progressive disclosure sections, likely empty/draft state shell.
3. **Authoring-specific UI primitives/components**
   - New components for section cards, guided prompts, step groups, metadata callouts, or stage navigation if needed.
   - Keep these isolated from `NewSessionPage` so later slices can build on them.
4. **Frontend tests for product contract**
   - New page tests for route rendering, section language, and discoverability.
   - Preserve or carefully update `NewSessionPage` tests to keep menu-intent flow intact.

## What To Build or Prove First
1. **Prove the authored path can be added without regressing `/sessions/new`.**
   - This is the main product-boundary risk called out in milestone context.
2. **Establish the chef-language information architecture.**
   - Before any backend/domain binding, decide the page sections and wording that map later to structure without exposing schema terminology.
3. **Create component seams that later slices can wire into native recipe contract work.**
   - S02/S03 will need stable UI sections to attach real fields and validation guidance.

## Risks / Constraints
- **Biggest risk:** accidentally reintroducing cookbook-mode UX through existing legacy types because they already exist in `DinnerConcept` and generator tests. That would satisfy neither the milestone intent nor D044.
- `NewSessionPage` test suite currently encodes the M015 de-scope. A planner that edits this page directly must do so carefully and intentionally.
- There is no authored-recipe database seam yet. If S01 tries to persist drafts or recipes now, scope will sprawl into S02/S04.
- Session lifecycle/status ownership is critical in this repo. Do not invent intermediate session statuses or overload existing `/sessions` behavior for draft authoring.

## Verification
Use repo-root commands; keep backend and frontend verification separate.

### Frontend
- `npm --prefix frontend run test -- NewSessionPage`
- `npm --prefix frontend run test -- <new-authored-workspace-test-file>`
- `npm --prefix frontend run build`
- Optional targeted lint if files change broadly: `npm --prefix frontend run lint`

### Backend
- If S01 stays frontend-only, backend verification should be unnecessary beyond avoiding contract changes.
- If any session-route/types are touched, run targeted repo-root pytest, e.g.:
  - `./.venv/bin/python -m pytest tests/test_status_projection.py -v`
  - plus any route tests added/updated.

### Notes from research verification
- A mixed command that passed the frontend Vitest file to `pytest` failed with “not found”, which confirms the project’s verification split: **frontend tests run through npm/Vitest, backend tests through repo-root pytest**.

## Planner Notes
- Treat S01 as a **UX boundary slice**, not a persistence slice.
- Prefer adding a **new authored workspace route/page** over turning `/sessions/new` into a mode switcher.
- If discoverability needs a dashboard change, keep the current “Plan a Dinner” CTA intact and add a parallel authored-recipe entry rather than renaming the whole product around authoring.
- Reserve recipe-structure capture, validation rules, persistence, cookbook containers, and scheduling integration for later slices; only create the UI seams that make those next steps straightforward.
