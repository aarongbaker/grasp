"""api/routes/users.py — User profile CRUD"""

import logging
import uuid
from datetime import datetime, timezone

import bcrypt
from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.core.settings import get_settings
from app.models.enums import EquipmentCategory
from app.models.invite import Invite
from app.models.user import BurnerDescriptor, Equipment, KitchenConfig, UserProfile

logger = logging.getLogger(__name__)

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
    invite_code: str | None = None


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
    burners: list[BurnerDescriptor] | None = None

    @model_validator(mode="after")
    def _validate_second_oven_rack_fields(self):
        if self.has_second_oven is False and self.max_second_oven_racks is not None:
            raise ValueError("max_second_oven_racks requires has_second_oven=true")
        return self


class UpdateDietaryDefaultsRequest(BaseModel):
    dietary_defaults: list[str]


class EquipmentRequest(BaseModel):
    name: str
    category: EquipmentCategory
    unlocks_techniques: list[str] = []


@router.post("", status_code=201, response_model=UserResponse)
async def create_user(body: CreateUserRequest, db: DBSession):
    email = body.email.strip().lower()
    settings = get_settings()

    # Invite validation if invite gating is enabled
    if settings.invite_codes_enabled:
        if not body.invite_code:
            raise HTTPException(status_code=400, detail="Invite code is required")

        invite_result = await db.exec(select(Invite).where(Invite.code == body.invite_code))
        invite = invite_result.first()

        if not invite:
            raise HTTPException(status_code=400, detail="Invalid invite code")

        if invite.claimed_at is not None:
            raise HTTPException(status_code=400, detail="Invite code has already been used")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if invite.expires_at <= now:
            raise HTTPException(status_code=400, detail="Invite code has expired")

        if invite.email.strip().lower() != email:
            raise HTTPException(status_code=400, detail="Invite code does not match email address")

        invite.claimed_at = now
        db.add(invite)

    # Check for duplicate email before attempting insert
    existing = await db.exec(select(UserProfile).where(UserProfile.email == email))
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
        name=body.name.strip(),
        email=email,
        rag_owner_key=UserProfile.build_rag_owner_key(email),
        password_hash=_hash_password(body.password),
        kitchen_config_id=kitchen.kitchen_config_id,
        dietary_defaults=body.dietary_defaults,
    )
    db.add(user)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists")

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
    from app.models.session import Session

    try:
        result = await db.exec(select(Session).where(Session.user_id == user_id).order_by(Session.created_at.desc()))
        sessions = result.all()
        logger.info("list_sessions: found %d sessions for user %s", len(sessions), user_id)
        return sessions
    except Exception:
        logger.exception("list_sessions failed for user %s", user_id)
        raise


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

    updated_kitchen = kc.model_dump(exclude={"user"})
    updated_kitchen.update(body.model_dump(exclude_unset=True))
    try:
        validated_kitchen = KitchenConfig.model_validate(updated_kitchen)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=jsonable_encoder(exc.errors())) from exc

    kc.max_burners = validated_kitchen.max_burners
    kc.max_oven_racks = validated_kitchen.max_oven_racks
    kc.has_second_oven = validated_kitchen.has_second_oven
    kc.max_second_oven_racks = validated_kitchen.max_second_oven_racks
    kc.burners = validated_kitchen.burners

    await db.commit()
    await db.refresh(kc)
    return {
        "kitchen_config_id": str(kc.kitchen_config_id),
        "max_burners": kc.max_burners,
        "max_oven_racks": kc.max_oven_racks,
        "has_second_oven": kc.has_second_oven,
        "max_second_oven_racks": kc.max_second_oven_racks,
        "burners": [burner.model_dump() for burner in kc.burners],
    }


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

    existing_equipment = await db.exec(select(Equipment).where(Equipment.user_id == user_id))
    if len(existing_equipment.all()) >= 20:
        raise HTTPException(status_code=400, detail="Equipment limit exceeded: maximum 20 items")

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
