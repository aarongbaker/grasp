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


class UpdateKitchenRequest(BaseModel):
    max_burners: int | None = Field(default=None, ge=1, le=10)
    max_oven_racks: int | None = Field(default=None, ge=1, le=6)
    has_second_oven: bool | None = None
    max_second_oven_racks: int | None = Field(default=None, ge=1, le=6)


class UpdateDietaryDefaultsRequest(BaseModel):
    dietary_defaults: list[str]


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
    # Eagerly load relationships that aren't available via async lazy loading
    from sqlalchemy.orm import selectinload

    result = await db.exec(
        select(UserProfile)
        .where(UserProfile.user_id == user_id)
        .options(selectinload(UserProfile.kitchen_config), selectinload(UserProfile.equipment))
    )
    user = result.first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    data = user.model_dump(exclude={"password_hash"})
    # model_dump() doesn't serialize relationships — add them manually
    data["kitchen_config"] = user.kitchen_config.model_dump() if user.kitchen_config else None
    data["equipment"] = [eq.model_dump() for eq in user.equipment]
    return data


class SessionListItem(BaseModel):
    """Lightweight response for the session list — excludes heavy result columns."""
    model_config = {"from_attributes": True}

    session_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    concept_json: dict
    schedule_summary: str | None = None
    total_duration_minutes: int | None = None
    error_summary: str | None = None
    celery_task_id: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


@router.get("/{user_id}/sessions", response_model=list[SessionListItem])
async def list_sessions(user_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    from models.session import Session

    result = await db.exec(select(Session).where(Session.user_id == user_id).order_by(Session.created_at.desc()))
    return result.all()


@router.patch("/{user_id}/kitchen")
async def update_kitchen(user_id: uuid.UUID, body: UpdateKitchenRequest, db: DBSession, current_user: CurrentUser):
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Load kitchen_config since async sessions don't support lazy loading
    kc = None
    if current_user.kitchen_config_id:
        result = await db.exec(select(KitchenConfig).where(KitchenConfig.kitchen_config_id == current_user.kitchen_config_id))
        kc = result.first()
    if not kc:
        kc = KitchenConfig()
        db.add(kc)
        await db.flush()
        current_user.kitchen_config_id = kc.kitchen_config_id

    if body.max_burners is not None:
        kc.max_burners = body.max_burners
    if body.max_oven_racks is not None:
        kc.max_oven_racks = body.max_oven_racks
    if body.has_second_oven is not None:
        kc.has_second_oven = body.has_second_oven
    if body.max_second_oven_racks is not None:
        kc.max_second_oven_racks = body.max_second_oven_racks

    await db.commit()
    await db.refresh(kc)
    return {"kitchen_config_id": str(kc.kitchen_config_id), "max_burners": kc.max_burners, "max_oven_racks": kc.max_oven_racks, "has_second_oven": kc.has_second_oven, "max_second_oven_racks": kc.max_second_oven_racks}


@router.put("/{user_id}/dietary-defaults")
async def update_dietary_defaults(user_id: uuid.UUID, body: UpdateDietaryDefaultsRequest, db: DBSession, current_user: CurrentUser):
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    current_user.dietary_defaults = body.dietary_defaults
    await db.commit()
    return {"dietary_defaults": current_user.dietary_defaults}


@router.post("/{user_id}/equipment", status_code=201)
async def add_equipment(user_id: uuid.UUID, body: EquipmentRequest, db: DBSession, current_user: CurrentUser):
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    eq = Equipment(user_id=user_id, name=body.name, category=body.category, unlocks_techniques=body.unlocks_techniques)
    db.add(eq)
    await db.commit()
    await db.refresh(eq)
    return {"equipment_id": str(eq.equipment_id), "user_id": str(eq.user_id), "name": eq.name, "category": eq.category, "unlocks_techniques": eq.unlocks_techniques}


@router.delete("/{user_id}/equipment/{equipment_id}", status_code=204)
async def delete_equipment(user_id: uuid.UUID, equipment_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    result = await db.exec(select(Equipment).where(Equipment.equipment_id == equipment_id, Equipment.user_id == user_id))
    eq = result.first()
    if not eq:
        raise HTTPException(status_code=404, detail="Equipment not found")

    await db.delete(eq)
    await db.commit()
