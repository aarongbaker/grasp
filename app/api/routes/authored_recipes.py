"""api/routes/authored_recipes.py — Private authored recipe draft persistence.

Stores chef-authored recipes as user-owned GRASP records behind an authenticated
route family that is intentionally separate from /sessions and its lifecycle
status contract.
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
    payload = body.model_dump(mode="json")
    payload.pop("user_id", None)
    payload.pop("cookbook_id", None)
    return payload


def _cookbook_summary(record: RecipeCookbookRecord | None) -> AuthoredRecipeCookbookSummary | None:
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
    if cookbook_id is None:
        return None

    cookbook = await db.get(RecipeCookbookRecord, cookbook_id)
    if cookbook is None:
        raise HTTPException(status_code=404, detail="Cookbook not found")
    if cookbook.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return cookbook


def _read_model_from_record(
    record: AuthoredRecipeRecord,
    cookbook: RecipeCookbookRecord | None = None,
) -> AuthoredRecipeRead:
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
    result = await db.exec(
        select(AuthoredRecipeRecord)
        .where(AuthoredRecipeRecord.user_id == current_user.user_id)
        .order_by(AuthoredRecipeRecord.updated_at.desc())
    )
    records = result.all()

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
