"""api/routes/authored_recipes.py — Private authored recipe draft persistence.

Stores chef-authored recipes as user-owned GRASP records behind an authenticated
route family that is intentionally separate from /sessions and its lifecycle
status contract.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.models.authored_recipe import (
    AuthoredRecipeCreate,
    AuthoredRecipeRead,
    AuthoredRecipeRecord,
)

router = APIRouter(prefix="/authored-recipes")


class AuthoredRecipeListItem(BaseModel):
    recipe_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    cuisine: str
    created_at: datetime
    updated_at: datetime


def _payload_from_contract(body: AuthoredRecipeCreate) -> dict:
    payload = body.model_dump(mode="json")
    payload.pop("user_id", None)
    return payload


def _read_model_from_record(record: AuthoredRecipeRecord) -> AuthoredRecipeRead:
    payload = dict(record.authored_payload or {})
    return AuthoredRecipeRead.model_validate(
        {
            **payload,
            "recipe_id": record.recipe_id,
            "user_id": record.user_id,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )


@router.post("", status_code=201, response_model=AuthoredRecipeRead)
async def create_authored_recipe(body: AuthoredRecipeCreate, db: DBSession, current_user: CurrentUser):
    if body.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    record = AuthoredRecipeRecord(
        user_id=current_user.user_id,
        title=body.title,
        description=body.description,
        cuisine=body.cuisine,
        authored_payload=_payload_from_contract(body),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _read_model_from_record(record)


@router.get("", response_model=list[AuthoredRecipeListItem])
async def list_authored_recipes(db: DBSession, current_user: CurrentUser):
    result = await db.exec(
        select(AuthoredRecipeRecord)
        .where(AuthoredRecipeRecord.user_id == current_user.user_id)
        .order_by(AuthoredRecipeRecord.updated_at.desc())
    )
    records = result.all()
    return [
        AuthoredRecipeListItem(
            recipe_id=record.recipe_id,
            user_id=record.user_id,
            title=record.title,
            cuisine=record.cuisine,
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
    return _read_model_from_record(record)
