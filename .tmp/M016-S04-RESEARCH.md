# S04: Private recipe library and cookbook containers

## Summary
S04 primarily advances **R026 (private recipe library for authored recipes)** and **R027 (cookbook artifacts as recipe-organizing containers)**. The current authored-recipe seam from S02/S03 already gives us private user-owned recipe records on `/api/v1/authored-recipes`, but it stops at create/list/read plus a single-record reopen flow inside `/recipes/new`. There is no browseable library page, no cookbook/container domain model, no recipe-to-container assignment seam, and no frontend navigation for private recipe organization yet.

This is **targeted research**, not deep architecture work. The repo already established the right pattern in D047 and S02: keep this domain separate from `/sessions`, use authenticated user-owned route families, and preserve the chef-language `/recipes/new` workspace. S04 should extend that pattern with a dedicated library/container seam rather than stuffing organization controls into session models or reviving old public cookbook upload/product paths.

## Recommendation
Build S04 in three layers:

1. **Backend persistence + API seam first**
   - Add a private cookbook-container model/table and the minimal authored-recipe association needed to organize recipes.
   - Extend `/api/v1/authored-recipes` list/read responses with enough library metadata for browsing and reopening.
   - Add a separate authenticated route family for cookbook containers (`/api/v1/recipe-cookbooks` or similar), not `/sessions` and not old ingestion cookbook routes.

2. **Frontend library page second**
   - Add a separate protected route for browsing private authored recipes and cookbook containers.
   - Keep `/recipes/new` as the drafting/editor route; use the new page for retrieval, filtering, and container organization.

3. **Workspace handoff last**
   - Add light entry/exit seams between the library page and `/recipes/new` (open draft, create new draft, maybe save into container), but do not turn the workspace into the main browse surface.

This matches D044/D045/D047 and the frontend-design skill guidance: keep a clear product point of view, preserve chef-language, and avoid turning the interface into a technical CRUD dump.

## Implementation Landscape

### Existing backend seam
- `app/models/authored_recipe.py`
  - Defines the authored recipe contract and the persisted `AuthoredRecipeRecord` SQLModel table.
  - `AuthoredRecipeRecord` currently stores:
    - indexed `user_id`
    - indexed `title`
    - top-level `description`, `cuisine`
    - full nested JSON `authored_payload`
    - timestamps
  - There is **no cookbook/container field**, no library metadata beyond title/cuisine/timestamps, and no update model.
- `app/api/routes/authored_recipes.py`
  - Only supports:
    - `POST /authored-recipes`
    - `GET /authored-recipes`
    - `GET /authored-recipes/{recipe_id}`
  - Ownership enforcement is already correct and isolated from session lifecycle/status ownership.
  - `list_authored_recipes()` currently returns only `recipe_id`, `user_id`, `title`, `cuisine`, `created_at`, `updated_at`.
  - There is **no mutation path** for assigning a recipe to a cookbook/container.
- `alembic/versions/f7a8b9c0d1e2_add_authored_recipes_table.py`
  - Current authored recipe migration pattern is straightforward and local; S04 can follow it with another migration.
- `app/db/session.py` and `app/main.py`
  - New SQLModel tables/routes are registered by import in these seams.
  - S04 will need the same metadata/router registration updates as S02 did.

### Existing frontend seam
- `frontend/src/pages/AuthoredRecipeWorkspacePage.tsx`
  - Already acts as the drafting/editor surface.
  - Supports save and reopen-by-ID only.
  - Keeps strong chef-language and progressive disclosure from S01/S03.
  - The page is already large; using it as the full library browser would be awkward.
- `frontend/src/api/authoredRecipes.ts`
  - Mirrors backend authored-recipe routes with create/list/read helpers.
  - No update/organize helpers yet.
- `frontend/src/types/api.ts`
  - Has authored recipe detail/list item types only.
  - Will need cookbook/container types and likely richer library list item types.
- `frontend/src/App.tsx`
  - Has `/recipes/new` but no recipe library route.
- `frontend/src/components/layout/Sidebar.tsx`
  - Current nav label is `Recipe Drafts` pointing to `/recipes/new`.
  - This will likely need to become a real library entry, with a separate CTA for new draft creation.
- `frontend/src/pages/DashboardPage.tsx`
  - Current creation rail has “Start a Recipe Draft” → `/recipes/new`.
  - S04 likely needs an additional “Browse recipe library” or a rerouted authored-recipe CTA once the library exists.

### Existing test seam
- `tests/test_api_routes.py`
  - Already has a `MockDBSession` with explicit authored-recipe `exec()` handling for select queries.
  - This is the fastest backend verification seam for S04.
  - Likely needs extension for cookbook-container selects and assignment/list behavior.
- `frontend/src/pages/__tests__/AuthoredRecipeWorkspacePage.test.tsx`
  - Covers save/reopen/chef-language validation on `/recipes/new`.
  - S04 should add new page tests rather than overload this suite too much.

## Natural seams for the planner

### 1. Persistence/domain task
Files likely touched:
- `app/models/authored_recipe.py` or a new `app/models/recipe_library.py`
- `app/db/session.py`
- `alembic/env.py`
- new Alembic migration in `alembic/versions/`

Questions to settle in planning:
- **Where should cookbook containers live?**
  - Cleanest option: a new SQLModel table for private user-owned cookbook containers.
- **How should recipe assignment work?**
  - Likely simplest: nullable foreign key on `authored_recipes` to one cookbook container.
  - This matches the milestone language of cookbook artifacts behaving like folders.
  - It is cheaper than a many-to-many join and enough for `Dessert` / `Mexican` organization.
  - If future multi-collection tagging is needed, that can be revisited later.

Why this is the right first proof:
- R026/R027 are impossible without a durable persisted owner-scoped organization seam.
- The repo already prefers JSON payload + indexed list fields for early user-owned domains (S02 pattern).
- A one-cookbook-per-recipe folder model is honest to M016 scope and avoids accidentally designing a marketplace taxonomy system.

### 2. Backend API task
Files likely touched:
- `app/api/routes/authored_recipes.py`
- new route file for cookbook containers, likely `app/api/routes/recipe_cookbooks.py`
- `app/main.py`
- `tests/test_api_routes.py`

Expected responsibilities:
- Add cookbook container create/list/read endpoints with ownership enforcement.
- Extend authored recipe list/read surfaces to expose cookbook assignment metadata.
- Add an update or assignment endpoint for moving a recipe into a cookbook container.

Recommended shape:
- Keep cookbook-container operations on their own route family.
- Keep recipe assignment/update on the authored-recipe route family if it is specifically recipe metadata.
- Do not touch `/sessions`, `DinnerConcept`, or session status code.

### 3. Frontend library UX task
Files likely touched:
- new page, likely `frontend/src/pages/RecipeLibraryPage.tsx`
- new CSS module for that page
- `frontend/src/api/authoredRecipes.ts`
- possibly new `frontend/src/api/recipeCookbooks.ts`
- `frontend/src/types/api.ts`
- `frontend/src/App.tsx`
- `frontend/src/components/layout/Sidebar.tsx`
- `frontend/src/pages/DashboardPage.tsx`
- new page tests under `frontend/src/pages/__tests__/`

Recommended UX split:
- `/recipes` or similar = browse private recipe library and cookbook containers.
- `/recipes/new` = author/edit workspace.
- Keep the workspace’s chef-notebook tone from S01/S03.
- Use the library page for:
  - showing saved authored recipes
  - showing cookbook containers like Dessert/Mexican
  - assigning or filtering recipes by container
  - opening a recipe back into the workspace

Why this split matters:
- The workspace is already optimized for drafting, not browsing.
- The dashboard/sidebar currently collapse “recipe drafts” and “recipe library” into one place; S04 is the slice that should untangle that.
- This follows D045/D047’s principle of preserving clean product boundaries rather than overloading existing routes.

## Constraints and decisions to preserve
- **D044**: keep chef-language labels and progressive disclosure; do not expose raw schema or admin terminology.
- **D047**: keep authored recipes on a dedicated `/api/v1/authored-recipes` seam instead of touching `/sessions`.
- **Milestone context**: cookbook artifacts in M016 are **private organizational containers**, not public/shared cookbooks and not the old upload/product surface.
- **Project knowledge K001/K003** and skill preference:
  - CSS Modules only.
  - Warm editorial UI conventions from `CLAUDE.md` remain in force.
- **frontend-design skill guidance** applies here:
  - keep a clear aesthetic direction,
  - preserve distinctive typography/color choices already established in the app,
  - avoid generic CRUD/admin-list aesthetics.

## Likely data shape
Most likely minimal viable contract for S04:

### New cookbook container model
- `cookbook_id`
- `user_id`
- `title`
- optional `description`
- timestamps

### Authored recipe list/read expansion
- cookbook assignment metadata on each authored recipe:
  - `cookbook_id` nullable
  - maybe `cookbook_title` on list items for cheap rendering

This keeps the current S02 JSON payload strategy intact while adding only the list/browse fields S04 needs.

## Risks / watchpoints
- **Do not revive old public cookbook/upload language.**
  - `app/ingestion/*` and old cookbook-related pipeline code remain explicitly admin/internal after M015.
  - S04 should not attach to those seams.
- **Do not force organization into the authoring payload JSON.**
  - Cookbook container assignment is library metadata, not recipe content.
  - It belongs on the persisted record/relational seam, not inside `authored_payload`.
- **Do not overload `/recipes/new` with full library behavior.**
  - That page is already large and focused on authoring/validation.
- **Do not leak session semantics.**
  - No session status fields, no `DinnerConcept`, no `POST /sessions` changes.

## Verification
Authoritative checks should stay repo-root and mirror S02/S03 patterns:

### Backend
- `./.venv/bin/python -m pytest tests/test_api_routes.py -x --tb=short -k "authored_recipe or authored_recipes or cookbook"`
- If a focused new backend test file is added instead, that focused repo-root pytest command is fine.

### Frontend
- `npm --prefix frontend run test -- RecipeLibraryPage`
- `npm --prefix frontend run test -- AuthoredRecipeWorkspacePage`
- `npm --prefix frontend run build`

### Slice-level confidence check
- `./.venv/bin/python -m pytest tests/ -x --tb=short`

UAT-worthy behaviors to prove:
- a user can create at least one cookbook container
- a saved authored recipe appears in the private library
- the user can assign/move a recipe into a cookbook container
- the library view shows grouped/filterable organization without touching dinner-planning sessions
- the user can reopen a saved recipe from the library back into `/recipes/new`

## Planner-ready task order
1. **Persistence first** — cookbook-container table + authored-recipe assignment seam.
2. **Backend route layer second** — cookbook CRUD/list plus recipe assignment/list metadata.
3. **Frontend library route third** — new browse page, nav updates, API/types.
4. **Workspace handoff and regression coverage last** — open-from-library flow, preserve `/recipes/new` save/reopen behavior.

## Skills Discovered
- No additional directly relevant external skill was worth installing.
- Used existing `frontend-design` skill guidance only; it reinforced keeping the library page intentional and chef-facing rather than a generic admin CRUD table.
