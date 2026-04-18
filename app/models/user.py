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
from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.types import TypeDecorator
from sqlmodel import Column, Field, Relationship, SQLModel

from app.models.enums import EquipmentCategory, SessionStatus


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

    @field_validator("burners", mode="before")
    @classmethod
    def _coerce_burners_to_jsonable(cls, value):
        if value is None:
            return []
        coerced = []
        for burner in value:
            if isinstance(burner, BurnerDescriptor):
                coerced.append(burner)
            elif isinstance(burner, dict):
                coerced.append(BurnerDescriptor.model_validate(burner))
            else:
                model_dump = getattr(burner, "model_dump", None)
                if callable(model_dump):
                    coerced.append(BurnerDescriptor.model_validate(model_dump()))
                else:
                    coerced.append(BurnerDescriptor.model_validate(burner))
        return coerced

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


class CatalogPurchaseState(str, Enum):
    """App-owned lifecycle for one catalog cookbook purchase attempt."""

    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CatalogPurchaseProvider(str, Enum):
    """Provider enum for catalog purchases kept app-owned and replaceable."""

    APP = "app"
    STRIPE = "stripe"


class SellerPayoutOnboardingStatus(str, Enum):
    """App-owned snapshot of a chef's seller payout readiness."""

    NOT_STARTED = "not_started"
    INCOMPLETE = "incomplete"
    PENDING_REVIEW = "pending_review"
    ENABLED = "enabled"
    RESTRICTED = "restricted"


class MarketplaceCookbookPublicationStatus(str, Enum):
    """Lifecycle for a cookbook publication into the public marketplace catalog."""

    DRAFT = "draft"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    ARCHIVED = "archived"


class LibraryAccessSummary(BaseModel):
    """Provider-agnostic account-facing cookbook library access contract."""

    state: LibraryAccessState
    reason: str = PydanticField(min_length=1, max_length=300)
    has_catalog_access: bool
    billing_state_changed: bool
    access_diagnostics: dict[str, str | None]


class GenerationBillingState(str, Enum):
    """App-owned lifecycle for one session's post-finalisation billing record."""

    READY = "ready"
    SKIPPED = "skipped"
    CHARGE_PENDING = "charge_pending"
    CHARGED = "charged"
    CHARGE_FAILED = "charge_failed"


class GenerationBillingProvider(str, Enum):
    """Provider enum kept app-owned so the ledger can outlive any vendor swap."""

    APP = "app"
    STRIPE = "stripe"


class GenerationFundingGrantType(str, Enum):
    """Durable user-owned funding buckets ordered before card fallback."""

    SUBSCRIPTION_CREDIT = "subscription_credit"
    PREPAID_BALANCE = "prepaid_balance"


class GenerationFundingGrantSource(str, Enum):
    """Why a funding grant exists from the app's point of view."""

    SUBSCRIPTION = "subscription"
    PACK = "pack"
    ADMIN = "admin"
    MIGRATION = "migration"


class GenerationFundingGrantState(str, Enum):
    """Lifecycle for a funding grant."""

    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class GenerationFundingLedgerEntryKind(str, Enum):
    """Ledger movements for funding grants and per-session settlement."""

    CREDIT = "credit"
    DEBIT = "debit"
    ADJUSTMENT = "adjustment"


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
    generation_payment_method_required: bool = Field(default=False)
    has_saved_generation_payment_method: bool = Field(default=False)
    default_generation_payment_method_label: Optional[str] = Field(default=None, max_length=120)
    monthly_free_generations_remaining: int = Field(default=0, ge=0)

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
    generation_billing_records: list["GenerationBillingRecord"] = Relationship(back_populates="user")
    generation_funding_grants: list["GenerationFundingGrant"] = Relationship(back_populates="user")
    generation_funding_ledger_entries: list["GenerationFundingLedgerEntry"] = Relationship(back_populates="user")
    catalog_purchase_records: list["CatalogCookbookPurchaseRecord"] = Relationship(back_populates="user")
    catalog_cookbook_ownerships: list["CatalogCookbookOwnershipRecord"] = Relationship(back_populates="user")
    seller_payout_accounts: list["SellerPayoutAccountRecord"] = Relationship(back_populates="user")
    marketplace_cookbook_publications: list["MarketplaceCookbookPublicationRecord"] = Relationship(back_populates="chef")


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


class GenerationFundingGrant(SQLModel, table=True):
    __tablename__ = "generation_funding_grants"

    generation_funding_grant_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)

    grant_type: GenerationFundingGrantType
    source: GenerationFundingGrantSource
    grant_state: GenerationFundingGrantState = Field(default=GenerationFundingGrantState.ACTIVE)
    amount: int = Field(default=0, ge=0)
    remaining_amount: int = Field(default=0, ge=0)
    currency: str = Field(default="generation", min_length=1, max_length=20)
    priority_bucket: int = Field(default=0, ge=0)
    cycle_key: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=200)
    funding_metadata: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    user: Optional[UserProfile] = Relationship(back_populates="generation_funding_grants")
    ledger_entries: list["GenerationFundingLedgerEntry"] = Relationship(back_populates="funding_grant")


class GenerationBillingRecord(SQLModel, table=True):
    __tablename__ = "generation_billing_records"

    generation_billing_record_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(foreign_key="sessions.session_id", unique=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    funding_grant_id: Optional[uuid.UUID] = Field(default=None, foreign_key="generation_funding_grants.generation_funding_grant_id", index=True)

    session_status: SessionStatus = Field(sa_column=Column(String, nullable=False))
    billing_state: GenerationBillingState = Field(default=GenerationBillingState.READY)
    provider: GenerationBillingProvider = Field(default=GenerationBillingProvider.APP)

    billing_source_type: Optional[str] = Field(default=None, max_length=50)
    provider_charge_ref: Optional[str] = Field(default=None, max_length=255)
    provider_error_code: Optional[str] = Field(default=None, max_length=100)
    provider_error_message: Optional[str] = Field(default=None, max_length=500)
    billing_reason: Optional[str] = Field(default=None, max_length=200)

    total_input_tokens: int = Field(default=0)
    total_output_tokens: int = Field(default=0)
    token_usage_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    billing_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))

    charge_attempted_at: Optional[datetime] = None
    charged_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    user: Optional[UserProfile] = Relationship(back_populates="generation_billing_records")
    funding_grant: Optional[GenerationFundingGrant] = Relationship()
    funding_entries: list["GenerationFundingLedgerEntry"] = Relationship(back_populates="billing_record")


class GenerationFundingLedgerEntry(SQLModel, table=True):
    __tablename__ = "generation_funding_ledger_entries"

    generation_funding_ledger_entry_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    session_id: Optional[uuid.UUID] = Field(default=None, foreign_key="sessions.session_id", index=True)
    generation_billing_record_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="generation_billing_records.generation_billing_record_id",
        index=True,
    )
    funding_grant_id: Optional[uuid.UUID] = Field(default=None, foreign_key="generation_funding_grants.generation_funding_grant_id", index=True)

    entry_kind: GenerationFundingLedgerEntryKind
    funding_source_type: str = Field(min_length=1, max_length=50)
    amount: int = Field(default=0)
    balance_after: Optional[int] = None
    description: Optional[str] = Field(default=None, max_length=200)
    entry_metadata: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    user: Optional[UserProfile] = Relationship(back_populates="generation_funding_ledger_entries")
    funding_grant: Optional[GenerationFundingGrant] = Relationship(back_populates="ledger_entries")
    billing_record: Optional[GenerationBillingRecord] = Relationship(back_populates="funding_entries")


class CatalogCookbookPurchaseRecord(SQLModel, table=True):
    __tablename__ = "catalog_cookbook_purchase_records"

    catalog_cookbook_purchase_record_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    catalog_cookbook_id: uuid.UUID = Field(index=True)

    provider: CatalogPurchaseProvider = Field(default=CatalogPurchaseProvider.APP)
    provider_checkout_ref: Optional[str] = Field(default=None, max_length=255, index=True)
    provider_completion_ref: Optional[str] = Field(default=None, max_length=255, index=True)
    purchase_state: CatalogPurchaseState = Field(default=CatalogPurchaseState.PENDING)
    access_reason: str = Field(min_length=1, max_length=200)
    purchase_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    failure_code: Optional[str] = Field(default=None, max_length=100)
    failure_message: Optional[str] = Field(default=None, max_length=500)
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    user: Optional[UserProfile] = Relationship(back_populates="catalog_purchase_records")
    ownerships: list["CatalogCookbookOwnershipRecord"] = Relationship(back_populates="purchase_record")


class CatalogCookbookOwnershipRecord(SQLModel, table=True):
    __tablename__ = "catalog_cookbook_ownership_records"

    catalog_cookbook_ownership_record_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    catalog_cookbook_id: uuid.UUID = Field(index=True)
    purchase_record_id: uuid.UUID = Field(
        foreign_key="catalog_cookbook_purchase_records.catalog_cookbook_purchase_record_id",
        unique=True,
        index=True,
    )

    ownership_source: str = Field(default="purchase", min_length=1, max_length=100)
    access_reason: str = Field(min_length=1, max_length=200)
    ownership_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    user: Optional[UserProfile] = Relationship(back_populates="catalog_cookbook_ownerships")
    purchase_record: Optional[CatalogCookbookPurchaseRecord] = Relationship(back_populates="ownerships")


class SellerPayoutAccountRecord(SQLModel, table=True):
    """Persisted seller payout onboarding snapshot for one chef.

    This stores the app's current understanding of payout readiness without
    making raw provider account objects part of public contracts. Provider refs
    stay private to backend billing code/routes.
    """

    __tablename__ = "seller_payout_account_records"

    seller_payout_account_record_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True, unique=True)

    onboarding_status: SellerPayoutOnboardingStatus = Field(default=SellerPayoutOnboardingStatus.NOT_STARTED)
    charges_enabled: bool = Field(default=False)
    payouts_enabled: bool = Field(default=False)
    details_submitted: bool = Field(default=False)
    provider_account_ref: Optional[str] = Field(default=None, max_length=255)
    requirements_due: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    status_reason: Optional[str] = Field(default=None, max_length=300)
    provider_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    last_provider_sync_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    user: Optional[UserProfile] = Relationship(back_populates="seller_payout_accounts")


class MarketplaceCookbookPublicationRecord(SQLModel, table=True):
    """Persisted listing that publishes a private recipe cookbook into the marketplace.

    Assumption: a marketplace cookbook is derived from exactly one chef-owned
    RecipeCookbookRecord source container. Publication metadata lives here so
    the private cookbook record can stay private and planner-focused.
    """

    __tablename__ = "marketplace_cookbook_publications"
    __table_args__ = (
        UniqueConstraint("chef_user_id", "source_cookbook_id", name="uq_marketplace_publications_chef_source"),
    )

    marketplace_cookbook_publication_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    chef_user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    source_cookbook_id: uuid.UUID = Field(foreign_key="recipe_cookbooks.cookbook_id", index=True)

    publication_status: MarketplaceCookbookPublicationStatus = Field(default=MarketplaceCookbookPublicationStatus.DRAFT)
    title: str = Field(min_length=1, max_length=200)
    subtitle: Optional[str] = Field(default=None, max_length=300)
    description: str = Field(min_length=1, max_length=4000)
    slug: str = Field(min_length=1, max_length=120, index=True)
    cover_image_url: Optional[str] = Field(default=None, max_length=500)
    list_price_cents: int = Field(ge=0)
    currency: str = Field(default="usd", min_length=3, max_length=3)
    recipe_count_snapshot: int = Field(default=0, ge=0)
    publication_notes: Optional[str] = Field(default=None, max_length=500)
    publication_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    published_at: Optional[datetime] = None
    unpublished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    chef: Optional[UserProfile] = Relationship(back_populates="marketplace_cookbook_publications")
