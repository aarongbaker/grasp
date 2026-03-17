"""api/routes/users.py — User profile CRUD"""

import uuid
from datetime import datetime

import bcrypt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from core.deps import CurrentUser, DBSession
from models.enums import EquipmentCategory
from models.user import Equipment, KitchenConfig, UserProfile

router = APIRouter(prefix="/users")


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


class CreateUserRequest(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=8)
    max_burners: int = Field(default=4, ge=1, le=10)
    max_oven_racks: int = Field(default=2, ge=1, le=6)
    has_second_oven: bool = False
    dietary_defaults: list[str] = []


class UserResponse(BaseModel):
    model_config = {"from_attributes": True}

    user_id: uuid.UUID
    name: str
    email: str
    kitchen_config_id: uuid.UUID | None = None
    dietary_defaults: list[str] = []
    created_at: datetime


class EquipmentRequest(BaseModel):
    name: str
    category: EquipmentCategory
    unlocks_techniques: list[str] = []


@router.post("", status_code=201, response_model=UserResponse)
async def create_user(body: CreateUserRequest, db: DBSession):
    # Check for duplicate email before attempting insert
    existing = await db.exec(select(UserProfile).where(UserProfile.email == body.email))
    if existing.first():
        raise HTTPException(status_code=409, detail="An account with this email already exists")

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
        password_hash=_hash_password(body.password),
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
    # Exclude password_hash from response
    data = current_user.model_dump(exclude={"password_hash"})
    return data


@router.get("/{user_id}/sessions")
async def list_sessions(user_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    from models.session import Session

    result = await db.exec(select(Session).where(Session.user_id == user_id).order_by(Session.created_at.desc()))
    return result.all()
