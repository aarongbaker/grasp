"""
api/routes/users.py — User profile CRUD.

Covers:
  POST /users                          — registration (with optional invite gate)
  GET  /users/{user_id}/profile        — full profile with kitchen + equipment
  GET  /users/{user_id}/sessions       — session list (lightweight, no result columns)
  PATCH /users/{user_id}/kitchen       — update kitchen config
  PUT  /users/{user_id}/dietary-defaults — replace dietary defaults
  POST /users/{user_id}/equipment      — add equipment
  DELETE /users/{user_id}/equipment/{id} — remove equipment

Authorization: all non-registration endpoints require current_user.user_id == user_id.
Users can only read/modify their own profile — no admin-level cross-user access here
(that's in admin.py).

Duplicate email handling: we do a SELECT before INSERT as a user-friendly check,
then also catch IntegrityError from the DB unique constraint as a race condition
safety net. Two-phase because Postgres returns a generic IntegrityError that
doesn't clearly distinguish unique violations from other integrity failures.

Equipment limit: 20 items max. The scheduler loads all equipment at session start;
an unbounded list would cause unbounded query results.
"""

import logging
import uuid
from datetime import datetime, timezone

import bcrypt
from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.core.settings import get_settings
from app.models.enums import EquipmentCategory
from app.models.invite import Invite
from app.models.user import (
    BurnerDescriptor,
    Equipment,
    KitchenConfig,
    LibraryAccessState,
    LibraryAccessSummary,
    SubscriptionStatus,
    SubscriptionSyncState,
    EntitlementKind,
    UserProfile,
)
from app.services.subscriptions import build_subscription_diagnostics, get_active_subscription_snapshot, list_user_entitlement_grants

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users")


def _derive_library_access_summary(
    *,
    subscription_status: SubscriptionStatus | None,
    sync_state: SubscriptionSyncState | None,
    has_premium_entitlement: bool,
    diagnostics: dict[str, str | None],
) -> LibraryAccessSummary:
    """Derive one provider-agnostic library access summary for the account surface."""

    if has_premium_entitlement:
        return LibraryAccessSummary(
            state=LibraryAccessState.INCLUDED,
            reason="Cookbook library access is included with your account.",
            has_catalog_access=True,
            billing_state_changed=False,
            access_diagnostics=diagnostics,
        )

    if sync_state == SubscriptionSyncState.FAILED:
        return LibraryAccessSummary(
            state=LibraryAccessState.UNAVAILABLE,
            reason="Cookbook library access is temporarily unavailable because your subscription state could not be refreshed.",
            has_catalog_access=False,
            billing_state_changed=True,
            access_diagnostics=diagnostics,
        )

    if subscription_status in {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.GRACE_PERIOD}:
        return LibraryAccessSummary(
            state=LibraryAccessState.LOCKED,
            reason="Your current subscription no longer includes cookbook library access.",
            has_catalog_access=False,
            billing_state_changed=True,
            access_diagnostics=diagnostics,
        )

    return LibraryAccessSummary(
        state=LibraryAccessState.LOCKED,
        reason="Cookbook library access is not included on this account.",
        has_catalog_access=False,
        billing_state_changed=False,
        access_diagnostics=diagnostics,
    )


def _hash_password(password: str) -> str:
    """Hash a password with bcrypt. bcrypt.gensalt() uses work factor 12 by default.

    Returns a utf-8 string suitable for storing in UserProfile.password_hash.
    bcrypt.checkpw() in auth.py handles verification.
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


class CreateUserRequest(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=8)
    max_burners: int = Field(default=4, ge=1, le=10)
    max_oven_racks: int = Field(default=2, ge=1, le=6)
    has_second_oven: bool = False
    dietary_defaults: list[str] = []
    invite_code: str | None = None  # Required when invite_codes_enabled=True in settings


class UserResponse(BaseModel):
    """Lightweight user response — excludes password_hash."""

    model_config = {"from_attributes": True}

    user_id: uuid.UUID
    name: str
    email: str
    kitchen_config_id: uuid.UUID | None = None
    dietary_defaults: list[str] = []
    created_at: datetime


class UpdateKitchenRequest(BaseModel):
    """Partial kitchen config update. All fields are optional — only provided fields are changed."""

    max_burners: int | None = Field(default=None, ge=1, le=10)
    max_oven_racks: int | None = Field(default=None, ge=1, le=6)
    has_second_oven: bool | None = None
    max_second_oven_racks: int | None = Field(default=None, ge=1, le=6)
    burners: list[BurnerDescriptor] | None = None

    @model_validator(mode="after")
    def _validate_second_oven_rack_fields(self):
        """Reject max_second_oven_racks without has_second_oven=true.

        Setting rack count for an oven you don't have would create phantom capacity
        in the scheduler. This cross-field check prevents that misconfiguration.
        """
        if self.has_second_oven is False and self.max_second_oven_racks is not None:
            raise ValueError("max_second_oven_racks requires has_second_oven=true")
        return self


class UpdateDietaryDefaultsRequest(BaseModel):
    dietary_defaults: list[str]  # Full replacement — not additive


class EquipmentRequest(BaseModel):
    name: str
    category: EquipmentCategory
    unlocks_techniques: list[str] = []  # Technique names this equipment enables (e.g. "sous vide")


@router.post("", status_code=201, response_model=UserResponse)
async def create_user(body: CreateUserRequest, db: DBSession):
    """Register a new user account.

    Invite gate: when invite_codes_enabled=True, the request must include a valid,
    unclaimed, unexpired, email-matching invite code. Invite is atomically claimed
    (claimed_at set) to prevent reuse.

    KitchenConfig is created atomically with the user — the scheduler requires a
    kitchen config to schedule sessions. db.flush() assigns the UUID without
    committing so we can link it to UserProfile in the same transaction.

    rag_owner_key: built from email at registration. This is a stable hash that
    persists across DB migrations and is used for Pinecone namespace isolation.
    See UserProfile.build_rag_owner_key() in models/user.py.
    """
    email = body.email.strip().lower()
    settings = get_settings()

    # Invite validation if invite gating is enabled.
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

        # Email must match the invite — invite codes are email-specific.
        # Case-insensitive comparison because email addresses are case-insensitive.
        if invite.email.strip().lower() != email:
            raise HTTPException(status_code=400, detail="Invite code does not match email address")

        # Mark invite as claimed atomically with user creation.
        # If the DB commit fails below, this update is also rolled back.
        invite.claimed_at = now
        db.add(invite)

    # User-friendly duplicate check before the INSERT attempt.
    # The IntegrityError catch below handles the race condition where two registrations
    # with the same email arrive simultaneously.
    existing = await db.exec(select(UserProfile).where(UserProfile.email == email))
    if existing.first():
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    # Create KitchenConfig first — UserProfile references it via kitchen_config_id.
    # db.flush() assigns the UUID so we can link it before committing.
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
        # rag_owner_key is derived from email — stable hash for Pinecone isolation.
        # Using email (not user_id) means the key survives user_id changes.
        rag_owner_key=UserProfile.build_rag_owner_key(email),
        password_hash=_hash_password(body.password),
        kitchen_config_id=kitchen.kitchen_config_id,
        dietary_defaults=body.dietary_defaults,
    )
    db.add(user)

    try:
        await db.commit()
    except IntegrityError:
        # Race condition: two registrations with the same email simultaneously.
        # The first one committed; the second hits the DB unique constraint.
        await db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    await db.refresh(user)
    return user


@router.get("/{user_id}/profile")
async def get_profile(user_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """Return full user profile including kitchen_config and equipment.

    Why selectinload? Async SQLAlchemy sessions don't support lazy loading —
    accessing relationship attributes outside the session context raises
    MissingGreenlet. selectinload issues a second query to load the relationship
    eagerly within the same session context.

    model_dump(exclude={"password_hash"}): excludes the bcrypt hash from the
    response. Never return password hashes to clients — they can be used for
    offline cracking even if bcrypt is slow.

    kitchen_config and equipment are added manually after model_dump() because
    SQLAlchemy relationships aren't included in model_dump() by default.
    """
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    from sqlalchemy.orm import selectinload

    result = await db.exec(
        select(UserProfile)
        .where(UserProfile.user_id == user_id)
        .options(selectinload(UserProfile.kitchen_config), selectinload(UserProfile.equipment))  # type: ignore[arg-type]
    )
    user = result.first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    subscription_snapshot = await get_active_subscription_snapshot(db, user_id=user.user_id)
    entitlement_grants = await list_user_entitlement_grants(db, user_id=user.user_id)
    diagnostics = build_subscription_diagnostics(subscription_snapshot)
    diagnostics_payload = {
        "subscription_snapshot_id": str(diagnostics.subscription_snapshot_id) if diagnostics.subscription_snapshot_id else None,
        "subscription_status": diagnostics.subscription_status.value if diagnostics.subscription_status else None,
        "sync_state": diagnostics.sync_state.value if diagnostics.sync_state else None,
        "provider": diagnostics.provider,
    }
    has_premium_entitlement = any(
        grant.kind == EntitlementKind.CATALOG_PREMIUM and grant.is_active
        for grant in entitlement_grants
    )
    library_access = _derive_library_access_summary(
        subscription_status=subscription_snapshot.status if subscription_snapshot else None,
        sync_state=subscription_snapshot.sync_state if subscription_snapshot else None,
        has_premium_entitlement=has_premium_entitlement,
        diagnostics=diagnostics_payload,
    )

    data = user.model_dump(exclude={"password_hash"})
    data["kitchen_config"] = user.kitchen_config.model_dump() if user.kitchen_config else None
    data["equipment"] = [eq.model_dump() for eq in user.equipment]
    data["library_access"] = library_access.model_dump()
    return data


class SessionListItem(BaseModel):
    """Lightweight response for the session list — excludes heavy result columns.

    result_schedule and result_recipes are excluded because they can be hundreds
    of KB per session. The list view only needs summary information to render
    session cards. Full results are fetched on demand via GET /sessions/{id}/results.
    """

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
    """List all sessions for a user, sorted by created_at descending.

    Uses SessionListItem to exclude result columns — see the model's docstring.
    The query orders by created_at DESC so the most recent session appears first.
    """
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    from app.models.session import Session

    try:
        result = await db.exec(select(Session).where(Session.user_id == user_id).order_by(desc(Session.created_at)))  # type: ignore[arg-type]
        sessions = result.all()
        logger.info("list_sessions: found %d sessions for user %s", len(sessions), user_id)
        return sessions
    except Exception:
        logger.exception("list_sessions failed for user %s", user_id)
        raise


@router.patch("/{user_id}/kitchen")
async def update_kitchen(user_id: uuid.UUID, body: UpdateKitchenRequest, db: DBSession, current_user: CurrentUser):
    """Update kitchen configuration. Partial update — only provided fields change.

    Why merge then validate instead of direct field assignment?
      KitchenConfig has cross-field validators (e.g. max_second_oven_racks requires
      has_second_oven=True). Merging the existing config with the patch and then
      calling model_validate() runs all validators on the resulting config.
      Direct field assignment would bypass model-level validators.

    Why load KitchenConfig separately instead of via relationship?
      Async sessions don't support lazy loading — we need an explicit query.
      If the user has no KitchenConfig (rare, only if creation failed), we create one.
    """
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    kc = None
    if current_user.kitchen_config_id:
        result = await db.exec(select(KitchenConfig).where(KitchenConfig.kitchen_config_id == current_user.kitchen_config_id))
        kc = result.first()
    if not kc:
        # Defensive: create KitchenConfig if missing (shouldn't happen after registration).
        kc = KitchenConfig()
        db.add(kc)
        await db.flush()
        current_user.kitchen_config_id = kc.kitchen_config_id

    # Merge current config with the patch fields.
    # exclude={"user"} prevents relationship back-reference from appearing in the dict.
    # exclude_unset=True on the patch means only explicitly provided fields are included.
    updated_kitchen = kc.model_dump(exclude={"user"})
    updated_kitchen.update(body.model_dump(exclude_unset=True))
    try:
        # Validate the merged config — catches cross-field constraint violations.
        validated_kitchen = KitchenConfig.model_validate(updated_kitchen)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=jsonable_encoder(exc.errors())) from exc

    # Apply validated values to the ORM object field by field.
    # Can't do kc = validated_kitchen because kc must remain the tracked SQLAlchemy instance.
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
    """Replace dietary defaults. PUT (full replacement) not PATCH (partial update).

    dietary_defaults is a simple list — no merging logic needed.
    The session creation route merges these defaults with per-session restrictions,
    so updating dietary_defaults affects all future sessions but not existing ones.
    """
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    current_user.dietary_defaults = body.dietary_defaults
    await db.commit()
    return {"dietary_defaults": current_user.dietary_defaults}


@router.post("/{user_id}/equipment", status_code=201)
async def add_equipment(user_id: uuid.UUID, body: EquipmentRequest, db: DBSession, current_user: CurrentUser):
    """Add a piece of equipment to the user's kitchen.

    Equipment limit: 20 items max to prevent unbounded DB reads in the scheduler.
    The Celery task loads all equipment at pipeline start — an unbounded list
    would make startup time unpredictable.

    unlocks_techniques: a list of technique names this equipment enables.
    The generator node reads these to understand what cooking methods are available
    (e.g. "sous vide" equipment unlocks low-temperature precision cooking).
    """
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
    """Remove a piece of equipment. Ownership is verified via user_id in the query.

    The WHERE clause includes both equipment_id AND user_id to prevent IDOR
    (Insecure Direct Object Reference) — a user can only delete their own equipment
    even if they correctly guess another user's equipment_id UUID.
    """
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    result = await db.exec(select(Equipment).where(Equipment.equipment_id == equipment_id, Equipment.user_id == user_id))
    eq = result.first()
    if not eq:
        raise HTTPException(status_code=404, detail="Equipment not found")

    await db.delete(eq)
    await db.commit()
