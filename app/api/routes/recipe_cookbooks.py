"""api/routes/recipe_cookbooks.py — Private cookbook containers for authored recipes."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.models.authored_recipe import RecipeCookbookCreate, RecipeCookbookRead, RecipeCookbookRecord

router = APIRouter(prefix="/recipe-cookbooks")


@router.post("", status_code=201, response_model=RecipeCookbookRead)
async def create_recipe_cookbook(body: RecipeCookbookCreate, db: DBSession, current_user: CurrentUser):
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
    result = await db.exec(
        select(RecipeCookbookRecord)
        .where(RecipeCookbookRecord.user_id == current_user.user_id)
        .order_by(RecipeCookbookRecord.updated_at.desc(), RecipeCookbookRecord.name.asc())
    )
    return [RecipeCookbookRead.model_validate(record, from_attributes=True) for record in result.all()]


@router.get("/{cookbook_id}", response_model=RecipeCookbookRead)
async def get_recipe_cookbook(cookbook_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    record = await db.get(RecipeCookbookRecord, cookbook_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Cookbook not found")
    if record.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return RecipeCookbookRead.model_validate(record, from_attributes=True)
