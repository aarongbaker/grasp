"""
models/user.py
UserProfile, KitchenConfig, Equipment — SQLModel → Postgres.

Pure SQLModel (not pure Pydantic) because these persist to Postgres.
Pipeline models (RawRecipe, GRASPState, etc.) are pure Pydantic —
they never touch the DB directly; LangGraph's checkpointer handles them.

KitchenConfig is snapshotted into GRASPState at session start so a
config change mid-run cannot corrupt an in-progress schedule.
"""

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field as PydanticField, field_validator, model_validator
from sqlalchemy import JSON
from sqlmodel import Column, Field, Relationship, SQLModel

from app.models.enums import EquipmentCategory


class Equipment(SQLModel, table=True):
    __tablename__ = "equipment"

    equipment_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # User-scoped: a user can only see their own equipment. Index for fast lookups
    # when building the initial pipeline state (equipment is passed to every session).
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)

    name: str
    category: EquipmentCategory

    # Each piece of equipment unlocks techniques the LLM generator can suggest.
    # Example: sous_vide → precise-temperature cooking; stand_mixer → laminated doughs.
    # Passed to generator.py's _format_equipment() for prompt inclusion.
    # Stored as JSON array rather than a join table — list is short and rarely queried independently.
    unlocks_techniques: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # ORM back-reference for eager loading via UserProfile.equipment
    user: Optional["UserProfile"] = Relationship(back_populates="equipment")


class BurnerDescriptor(SQLModel):
    """Stable burner metadata carried in kitchen_config snapshots and scheduling output.

    When a kitchen has named/positioned burners (e.g. front-left large, back-right small),
    the dag_merger uses BurnerDescriptors to assign specific burners to stovetop steps.
    This lets the rendered schedule say "use the back-right burner" instead of "burner_2".

    SQLModel (not table=True) — stored as JSON inside KitchenConfig.burners, not its own table.
    Also returned verbatim in ScheduledStep.burner for the frontend to display.
    """

    burner_id: str      # stable identifier for this burner slot (e.g. "front_left")
    position: Optional[str] = None  # human label (e.g. "front-left")
    size: Optional[str] = None      # "large", "medium", "small" — for heat capacity hints
    label: Optional[str] = None     # display label for the chef (e.g. "Induction 1")

    @field_validator("burner_id")
    @classmethod
    def _validate_burner_id(cls, value: str) -> str:
        # Strip whitespace — prevents invisible-character bugs in burner_id comparisons
        # when the dag_merger looks up assigned burners by ID.
        burner_id = value.strip()
        if not burner_id:
            raise ValueError("burner_id must not be empty")
        return burner_id


class KitchenConfig(SQLModel, table=True):
    __tablename__ = "kitchen_configs"

    kitchen_config_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # Resource pool sizes — consumed directly by dag_merger for capacity planning.
    # max_burners: STOVETOP pool capacity; max_oven_racks: OVEN pool capacity.
    # Defaults to a typical home kitchen: 4 burners, 2 racks, no second oven.
    max_burners: int = Field(default=4, ge=1, le=10)
    max_oven_racks: int = Field(default=2, ge=1, le=6)
    has_second_oven: bool = Field(default=False)
    max_second_oven_racks: int = Field(default=2, ge=1, le=6)

    # Optional named burner descriptors. When present, dag_merger assigns specific
    # burner slots to stovetop steps and exposes the metadata in ScheduledStep.burner.
    # When absent, max_burners defines a fungible pool and the merger synthesizes
    # generic burner_1..N IDs. Stored as JSON — short list, never queried independently.
    burners: list[BurnerDescriptor] = Field(default_factory=list, sa_column=Column(JSON))

    # One-to-one back-reference to the owning UserProfile
    user: Optional["UserProfile"] = Relationship(back_populates="kitchen_config")

    @model_validator(mode="after")
    def _validate_burner_count(self):
        # Catches misconfiguration where a chef adds more named burners than
        # max_burners allows — would silently drop some named burners in the scheduler.
        if len(self.burners) > self.max_burners:
            raise ValueError("burners count cannot exceed max_burners")
        return self


class SubscriptionStatus(str, Enum):
    """Normalized subscription lifecycle states persisted by the app."""

    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    GRACE_PERIOD = "grace_period"


class SubscriptionSyncState(str, Enum):
    """Observed sync health for the persisted subscription snapshot."""

    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


class EntitlementKind(str, Enum):
    """Provider-agnostic app capabilities granted to a user."""

    CATALOG_PREVIEW = "catalog_preview"
    CATALOG_PREMIUM = "catalog_premium"


class LibraryAccessState(str, Enum):
    """User-visible access state for the cookbook library surface."""

    INCLUDED = "included"
    LOCKED = "locked"
    UNAVAILABLE = "unavailable"


class LibraryAccessSummary(BaseModel):
    """Provider-agnostic account-facing cookbook library access contract."""

    state: LibraryAccessState
    reason: str = PydanticField(min_length=1, max_length=300)
    has_catalog_access: bool
    billing_state_changed: bool
    access_diagnostics: dict[str, str | None]


class UserProfile(SQLModel, table=True):
    __tablename__ = "user_profiles"

    @staticmethod
    def build_rag_owner_key(email: str) -> str:
        """Stable, environment-portable identity for Pinecone ownership filtering.

        rag_owner_key is derived from email rather than user_id UUID so it stays
        stable across database migrations, staging→production copies, and test
        data imports. If user_id was used as the Pinecone filter, a DB migration
        that re-generates UUIDs would make all existing cookbook vectors
        unreachable for that user.

        Format: "email:alice-example-com" (URL-slug normalized)
        """
        normalized = email.strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return f"email:{slug}"

    user_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    email: str = Field(unique=True, index=True)  # unique constraint enforced at DB level

    # Stable Pinecone ownership key. Set at registration, never changes.
    # Used instead of user_id for RAG chunk ownership filtering — see build_rag_owner_key().
    rag_owner_key: str = Field(index=True)

    # bcrypt hash. Default empty string means account has no password set yet
    # (e.g. OAuth-created accounts in future). auth.py checks for non-empty before bcrypt.
    password_hash: str = Field(default="")

    stripe_customer_id: str = Field(default="", max_length=255, index=True)

    kitchen_config_id: Optional[uuid.UUID] = Field(default=None, foreign_key="kitchen_configs.kitchen_config_id")

    # Dietary restrictions that are merged into every DinnerConcept at session creation.
    # This means the chef doesn't have to re-enter their restrictions each time.
    # Stored as a JSON array of strings (e.g. ["vegan", "no nuts"]).
    dietary_defaults: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # UTC naive — same pattern as Session.created_at
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    # ORM relationships — used for eager loading in workers/tasks.py
    kitchen_config: Optional[KitchenConfig] = Relationship(back_populates="user")
    equipment: list[Equipment] = Relationship(back_populates="user")
    subscription_snapshots: list["SubscriptionSnapshot"] = Relationship(back_populates="user")
    entitlement_grants: list["UserEntitlementGrant"] = Relationship(back_populates="user")


class SubscriptionSnapshot(SQLModel, table=True):
    __tablename__ = "subscription_snapshots"

    subscription_snapshot_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)

    provider: str = Field(min_length=1, max_length=50)
    provider_customer_ref: Optional[str] = Field(default=None, max_length=255)
    provider_subscription_ref: Optional[str] = Field(default=None, max_length=255)
    plan_code: Optional[str] = Field(default=None, max_length=100)

    status: SubscriptionStatus = Field(default=SubscriptionStatus.CANCELLED)
    sync_state: SubscriptionSyncState = Field(default=SubscriptionSyncState.PENDING)

    current_period_ends_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None
    sync_error_code: Optional[str] = Field(default=None, max_length=100)
    sync_error_message: Optional[str] = Field(default=None, max_length=500)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    user: Optional[UserProfile] = Relationship(back_populates="subscription_snapshots")


class UserEntitlementGrant(SQLModel, table=True):
    __tablename__ = "user_entitlement_grants"

    entitlement_grant_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)

    kind: EntitlementKind
    source: str = Field(min_length=1, max_length=100)
    is_active: bool = Field(default=True)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    user: Optional[UserProfile] = Relationship(back_populates="entitlement_grants")
