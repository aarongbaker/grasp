"""api/routes/authored_recipes.py — Private authored recipe draft persistence.

Stores chef-authored recipes as user-owned GRASP records behind an authenticated
route family that is intentionally separate from /sessions and its lifecycle
status contract.

Design decisions:
  - Recipes are stored as authored_payload (JSONB blob) alongside promoted
    top-level columns (title, description, cuisine) for list queries.
    This avoids schema migrations every time the recipe structure evolves.
  - Cookbook assignment is optional — recipes can exist unassigned and be
    moved to a cookbook later via PATCH /{recipe_id}/cookbook.
  - Ownership is verified twice for cookbook operations: once via the route
    parameter (current_user.user_id == body.user_id) and once via
    _require_owned_cookbook() which checks the cookbook's user_id. This
    prevents a user from assigning a recipe to another user's cookbook.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.models.authored_recipe import (
    AuthoredRecipeCookbookSummary,
    AuthoredRecipeCreate,
    AuthoredRecipeListItem,
    AuthoredRecipeRead,
    AuthoredRecipeRecord,
    AuthoredRecipeUpdateCookbook,
    RecipeCookbookRecord,
)

router = APIRouter(prefix="/authored-recipes")


def _payload_from_contract(body: AuthoredRecipeCreate) -> dict:
    """Build the authored_payload dict for storage from the create request.

    Excludes user_id and cookbook_id because those are stored in separate
    columns on AuthoredRecipeRecord — including them in authored_payload
    would create a source-of-truth conflict if either were updated later.
    mode="json" ensures nested Pydantic models are serialised to plain dicts
    (not Pydantic instances) so the result is safe for JSONB storage.
    """
    payload = body.model_dump(mode="json")
    payload.pop("user_id", None)
    payload.pop("cookbook_id", None)
    return payload


def _cookbook_summary(record: RecipeCookbookRecord | None) -> AuthoredRecipeCookbookSummary | None:
    """Build a lightweight cookbook summary for embedding in recipe responses.

    Recipes in list and detail responses include the cookbook name/description
    so the client doesn't need a separate GET /recipe-cookbooks/{id} call to
    show the cookbook label on a recipe card. None-safe — returns None if no
    cookbook is assigned.
    """
    if record is None:
        return None
    return AuthoredRecipeCookbookSummary(
        cookbook_id=record.cookbook_id,
        name=record.name,
        description=record.description,
    )


async def _require_owned_cookbook(
    cookbook_id: uuid.UUID | None,
    *,
    db: DBSession,
    current_user: CurrentUser,
) -> RecipeCookbookRecord | None:
    """Fetch and verify ownership of a cookbook, or return None if not specified.

    Separates the IDOR check from the route handlers — every operation that
    touches a cookbook goes through this helper. The keyword-only db/current_user
    parameters make call sites unambiguous about which arguments are which.

    Returns None when cookbook_id is None so callers can write:
      cookbook = await _require_owned_cookbook(body.cookbook_id, ...)
      record.cookbook_id = cookbook.cookbook_id if cookbook else None
    without special-casing the None case.
    """
    if cookbook_id is None:
        return None

    cookbook = await db.get(RecipeCookbookRecord, cookbook_id)
    if cookbook is None:
        raise HTTPException(status_code=404, detail="Cookbook not found")
    if cookbook.user_id != current_user.user_id:
        # Return 403 rather than 404 to indicate the resource exists but is
        # not accessible — consistent with the IDOR prevention pattern used
        # in equipment and session routes.
        raise HTTPException(status_code=403, detail="Access denied")
    return cookbook


def _read_model_from_record(
    record: AuthoredRecipeRecord,
    cookbook: RecipeCookbookRecord | None = None,
) -> AuthoredRecipeRead:
    """Build an AuthoredRecipeRead response from an ORM record + optional cookbook.

    Why not use model_validate(record, from_attributes=True)?
      AuthoredRecipeRead has fields that come from two sources:
        - Most fields come from authored_payload (JSONB blob)
        - recipe_id, user_id, cookbook_id, created_at, updated_at come from DB columns
        - cookbook (summary) is derived from a separate cookbook record lookup

      Pydantic's from_attributes mode reads from ORM attributes, which wouldn't
      unpack authored_payload correctly. Instead we manually merge the payload
      dict with the identity columns and the cookbook summary.

    The **payload spread puts authored_payload fields first, then the explicit
    DB column values override any same-named fields in the payload (defense
    against a stale payload having an old recipe_id or user_id).
    """
    payload = dict(record.authored_payload or {})
    cookbook_summary = _cookbook_summary(cookbook)
    return AuthoredRecipeRead.model_validate(
        {
            **payload,
            "recipe_id": record.recipe_id,
            "user_id": record.user_id,
            "cookbook_id": record.cookbook_id,
            "cookbook": cookbook_summary.model_dump(mode="json") if cookbook_summary else None,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )


@router.post("", status_code=201, response_model=AuthoredRecipeRead)
async def create_authored_recipe(body: AuthoredRecipeCreate, db: DBSession, current_user: CurrentUser):
    """Create a new authored recipe, optionally assigning it to a cookbook.

    body.user_id must match current_user.user_id — the client includes user_id
    in the request body so the API contract is self-documenting about ownership.
    The server-side check prevents a user from creating a recipe owned by another user
    even if they manipulate the request body.

    Title, description, and cuisine are stored as top-level columns for efficient
    list queries — the authored_payload JSONB blob stores the full recipe structure.
    """
    if body.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    cookbook = await _require_owned_cookbook(body.cookbook_id, db=db, current_user=current_user)
    record = AuthoredRecipeRecord(
        user_id=current_user.user_id,
        cookbook_id=cookbook.cookbook_id if cookbook else None,
        title=body.title,
        description=body.description,
        cuisine=body.cuisine,
        authored_payload=_payload_from_contract(body),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _read_model_from_record(record, cookbook=cookbook)


@router.get("", response_model=list[AuthoredRecipeListItem])
async def list_authored_recipes(db: DBSession, current_user: CurrentUser):
    """List all authored recipes for the current user, sorted by most recently updated.

    Cookbook lookup strategy: collect unique cookbook_ids from the result set,
    then fetch each cookbook once (sorted deterministically to prevent race conditions
    in test scenarios). This is N+1 in the number of distinct cookbooks, not records —
    typically 1-5 distinct cookbooks for a user, so this is acceptable without
    a JOIN. A future optimisation would use selectinload or a single IN() query.

    Returns AuthoredRecipeListItem (lightweight) not AuthoredRecipeRead (full payload)
    to avoid loading the full authored_payload for every recipe in the list view.
    """
    result = await db.exec(
        select(AuthoredRecipeRecord)
        .where(AuthoredRecipeRecord.user_id == current_user.user_id)
        .order_by(AuthoredRecipeRecord.updated_at.desc())
    )
    records = result.all()

    # Deduplicate cookbook_ids and sort for determinism (important for test reproducibility).
    cookbook_ids = sorted({record.cookbook_id for record in records if record.cookbook_id is not None}, key=str)
    cookbooks_by_id: dict[uuid.UUID, RecipeCookbookRecord] = {}
    for cookbook_id in cookbook_ids:
        cookbook = await db.get(RecipeCookbookRecord, cookbook_id)
        if cookbook is not None:
            cookbooks_by_id[cookbook_id] = cookbook

    return [
        AuthoredRecipeListItem(
            recipe_id=record.recipe_id,
            user_id=record.user_id,
            title=record.title,
            cuisine=record.cuisine,
            cookbook_id=record.cookbook_id,
            cookbook=_cookbook_summary(cookbooks_by_id.get(record.cookbook_id)) if record.cookbook_id else None,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        for record in records
    ]


@router.get("/{recipe_id}", response_model=AuthoredRecipeRead)
async def get_authored_recipe(recipe_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """Fetch the full authored recipe including cookbook summary.

    Ownership check before cookbook lookup — if the recipe doesn't belong to
    the current user, 403 before we even try to fetch the cookbook.
    _require_owned_cookbook() handles cookbook ownership separately, providing
    defense-in-depth: a user can't access a recipe via a cookbook they don't own.
    """
    record = await db.get(AuthoredRecipeRecord, recipe_id)
    if not record:
        raise HTTPException(status_code=404, detail="Authored recipe not found")
    if record.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    cookbook = await _require_owned_cookbook(record.cookbook_id, db=db, current_user=current_user)
    return _read_model_from_record(record, cookbook=cookbook)


@router.patch("/{recipe_id}/cookbook", response_model=AuthoredRecipeRead)
async def update_authored_recipe_cookbook(
    recipe_id: uuid.UUID,
    body: AuthoredRecipeUpdateCookbook,
    db: DBSession,
    current_user: CurrentUser,
):
    """Move a recipe to a different cookbook (or remove it from its current cookbook).

    PATCH on the /cookbook sub-resource, not a general recipe update.
    Only cookbook assignment is changeable here — recipe content updates
    would go through a separate PATCH /{recipe_id} endpoint (not yet implemented).

    body.cookbook_id = None removes the recipe from its current cookbook
    without deleting the recipe. This is the "uncategorised" state.

    updated_at is manually set here because SQLAlchemy's onupdate triggers
    don't always fire for partial field updates via db.add() without a full
    model flush. Explicit assignment ensures the list view sorts correctly.
    """
    record = await db.get(AuthoredRecipeRecord, recipe_id)
    if not record:
        raise HTTPException(status_code=404, detail="Authored recipe not found")
    if record.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    cookbook = await _require_owned_cookbook(body.cookbook_id, db=db, current_user=current_user)
    record.cookbook_id = cookbook.cookbook_id if cookbook else None
    record.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _read_model_from_record(record, cookbook=cookbook)
