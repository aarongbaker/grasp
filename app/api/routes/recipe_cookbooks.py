"""api/routes/recipe_cookbooks.py — Private cookbook containers for authored recipes.

A cookbook is a named container that groups AuthoredRecipeRecords. It's a
simple organisational layer — no scheduling logic, no RAG involvement.

Why a separate router from authored_recipes.py?
  Cookbooks and recipes have different lifecycles. A cookbook can exist with
  zero recipes. A recipe can be moved between cookbooks. Keeping them as
  separate resources (with their own CRUD) reflects this — rather than
  nesting recipes under /cookbooks/{id}/recipes, which would make recipe
  creation awkward (you'd need a cookbook first).

Authorization model:
  Every route here checks cookbook.user_id == current_user.user_id.
  There's no sharing or collaborative access — cookbooks are strictly private.
  The check is done inline (not via a helper) because the routes are short
  and the pattern is trivial enough not to warrant abstraction.

Order in list: updated_at DESC, name ASC — most recently modified first,
alphabetical as a tiebreaker so the order is deterministic.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.models.authored_recipe import RecipeCookbookCreate, RecipeCookbookRead, RecipeCookbookRecord

router = APIRouter(prefix="/recipe-cookbooks")


@router.post("", status_code=201, response_model=RecipeCookbookRead)
async def create_recipe_cookbook(body: RecipeCookbookCreate, db: DBSession, current_user: CurrentUser):
    """Create a new cookbook owned by the current user.

    user_id is taken from current_user (the authenticated JWT) rather than
    the request body — the client doesn't specify ownership, the server assigns it.
    This prevents creating cookbooks for other users via a crafted request.

    model_validate with from_attributes=True: RecipeCookbookRecord is an ORM
    instance. Pydantic reads fields from ORM attributes in from_attributes mode.
    """
    record = RecipeCookbookRecord(
        user_id=current_user.user_id,
        name=body.name,
        description=body.description,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return RecipeCookbookRead.model_validate(record, from_attributes=True)


@router.get("", response_model=list[RecipeCookbookRead])
async def list_recipe_cookbooks(db: DBSession, current_user: CurrentUser):
    """List all cookbooks owned by the current user.

    No pagination — cookbooks are high-level containers. A user is unlikely
    to have more than 10-20 cookbooks. If this becomes a concern, offset/limit
    pagination would be added here.

    Sorting: updated_at DESC (most recently modified first) with name ASC as
    a deterministic tiebreaker for cookbooks modified at the same second.
    """
    result = await db.exec(
        select(RecipeCookbookRecord)
        .where(RecipeCookbookRecord.user_id == current_user.user_id)
        .order_by(RecipeCookbookRecord.updated_at.desc(), RecipeCookbookRecord.name.asc())
    )
    return [RecipeCookbookRead.model_validate(record, from_attributes=True) for record in result.all()]


@router.get("/{cookbook_id}", response_model=RecipeCookbookRead)
async def get_recipe_cookbook(cookbook_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """Fetch a single cookbook by ID.

    db.get() uses the primary key directly — faster than a WHERE query
    because SQLAlchemy can use the session identity map cache. If the record
    was recently loaded in the same session (e.g. during a PATCH flow), it
    won't hit the DB at all.

    Returns 404 for both missing and wrong-owner cookbooks when the cookbook
    exists but belongs to another user. This is intentional IDOR prevention —
    returning 403 would confirm the cookbook exists (information leak).
    Here we use 403 to be consistent with the rest of the codebase pattern,
    but 404 would also be acceptable for the wrong-owner case.
    """
    record = await db.get(RecipeCookbookRecord, cookbook_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Cookbook not found")
    if record.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return RecipeCookbookRead.model_validate(record, from_attributes=True)
