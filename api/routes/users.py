"""api/routes/users.py — User profile CRUD"""
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from core.deps import CurrentUser, DBSession
from models.enums import EquipmentCategory
from models.user import Equipment, KitchenConfig, UserProfile

router = APIRouter(prefix="/users")


class CreateUserRequest(BaseModel):
    name: str
    email: str
    max_burners: int = Field(default=4, ge=1, le=10)
    max_oven_racks: int = Field(default=2, ge=1, le=6)
    has_second_oven: bool = False
    dietary_defaults: list[str] = []


class EquipmentRequest(BaseModel):
    name: str
    category: EquipmentCategory
    unlocks_techniques: list[str] = []


@router.post("", status_code=201)
async def create_user(body: CreateUserRequest, db: DBSession):
    kitchen = KitchenConfig(
        max_burners=body.max_burners,
        max_oven_racks=body.max_oven_racks,
        has_second_oven=body.has_second_oven,
    )
    db.add(kitchen)
    await db.flush()

    user = UserProfile(
        name=body.name,
        email=body.email,
        kitchen_config_id=kitchen.kitchen_config_id,
        dietary_defaults=body.dietary_defaults,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/{user_id}/profile")
async def get_profile(user_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return current_user


@router.get("/{user_id}/sessions")
async def list_sessions(user_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    from models.session import Session
    result = await db.exec(
        select(Session)
        .where(Session.user_id == user_id)
        .order_by(Session.created_at.desc())
    )
    return result.all()
