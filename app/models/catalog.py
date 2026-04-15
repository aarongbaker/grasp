"""Platform-managed and marketplace cookbook contract models.

These types represent cookbook catalog contracts surfaced to clients. They are
intentionally separate from:

- RecipeCookbookRecord: a chef-owned private container for authored recipes
- planner_cookbook_target: a planner request field that points at a private
  RecipeCookbookRecord owned by the current user
- historical PDF ingestion / cookbook chunks: raw ingestion data remains an
  implementation detail and is NOT the catalog API contract

The naming is explicitly catalog-scoped so future agents do not confuse this
surface with private-library cookbook ownership or planner request payloads.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AccessResolverDiagnostics(BaseModel):
    """Inspectable persistence seam for access resolution debugging."""

    subscription_snapshot_id: uuid.UUID | None = None
    subscription_status: str | None = None
    sync_state: str | None = None
    provider: str | None = None


class CatalogCookbookOwnershipStatus(BaseModel):
    """App-safe ownership diagnostic surfaced on catalog contracts.

    This stays strictly provider-safe: it communicates whether access is durable
    because of a completed ownership record without exposing checkout/session/
    event identifiers or raw provider payloads.
    """

    is_owned: bool = False
    ownership_source: str | None = None
    access_reason: str | None = None


class CatalogCookbookAccessState(str, Enum):
    """Derived per-user access state for a platform-managed catalog cookbook."""

    INCLUDED = "included"
    PREVIEW = "preview"
    LOCKED = "locked"


class CatalogCookbookAudience(str, Enum):
    """Fixture-backed audience tier used to derive access state."""

    INCLUDED = "included"
    PREVIEW = "preview"
    PREMIUM = "premium"
    MARKETPLACE = "marketplace"


class MarketplaceCookbookPublicationStatus(str, Enum):
    """Public-facing publication status for chef-authored marketplace listings."""

    DRAFT = "draft"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    ARCHIVED = "archived"


class MarketplaceCookbookPublicationSummary(BaseModel):
    """Provider-safe marketplace listing metadata derived from persisted publication rows."""

    marketplace_cookbook_publication_id: uuid.UUID
    chef_user_id: uuid.UUID
    source_cookbook_id: uuid.UUID
    publication_status: MarketplaceCookbookPublicationStatus
    slug: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    subtitle: Optional[str] = Field(default=None, max_length=300)
    description: str = Field(min_length=1, max_length=4000)
    cover_image_url: Optional[str] = Field(default=None, max_length=500)
    list_price_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    recipe_count_snapshot: int = Field(ge=0)
    publication_notes: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[datetime] = None


class MarketplaceCookbookPublicationRecordView(BaseModel):
    """Internal/backend-facing publication contract used by seller and catalog flows."""

    marketplace_cookbook_publication_id: uuid.UUID
    chef_user_id: uuid.UUID
    source_cookbook_id: uuid.UUID
    publication_status: MarketplaceCookbookPublicationStatus
    slug: str
    title: str
    subtitle: Optional[str] = None
    description: str
    cover_image_url: Optional[str] = None
    list_price_cents: int
    currency: str
    recipe_count_snapshot: int
    publication_notes: Optional[str] = None
    published_at: Optional[str] = None
    unpublished_at: Optional[str] = None


class CatalogCookbookSummary(BaseModel):
    """List-item contract for one platform-managed catalog cookbook."""

    catalog_cookbook_id: uuid.UUID
    slug: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    subtitle: Optional[str] = Field(default=None, max_length=300)
    cover_image_url: Optional[str] = Field(default=None, max_length=500)
    recipe_count: int = Field(ge=0)
    audience: CatalogCookbookAudience
    access_state: CatalogCookbookAccessState
    access_state_reason: str = Field(min_length=1, max_length=300)
    ownership: CatalogCookbookOwnershipStatus = Field(default_factory=CatalogCookbookOwnershipStatus)
    access_diagnostics: AccessResolverDiagnostics | None = None


class CatalogCookbookDetail(CatalogCookbookSummary):
    """Detail contract for one platform-managed catalog cookbook.

    The detail shape stays catalog-scoped and does not expose private-library or
    planner-only fields like RecipeCookbookRecord IDs or planner target modes.
    """

    description: str = Field(min_length=1, max_length=4000)
    sample_recipe_titles: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class CatalogCookbookListResponse(BaseModel):
    """Read-only list response wrapper for the catalog seam."""

    items: list[CatalogCookbookSummary] = Field(default_factory=list)


class CatalogCookbookDetailResponse(BaseModel):
    """Read-only detail response wrapper for the catalog seam."""

    item: CatalogCookbookDetail


class SellerPayoutReadinessSummary(BaseModel):
    """Provider-safe seller payout readiness contract."""

    onboarding_status: str = Field(min_length=1, max_length=40)
    can_accept_sales: bool
    charges_enabled: bool
    payouts_enabled: bool
    details_submitted: bool
    requirements_due: list[str] = Field(default_factory=list)
    status_reason: str | None = Field(default=None, max_length=300)
    has_onboarding_action: bool = False


class SellerPayoutOnboardingLinkResponse(BaseModel):
    """Action response for seller payout onboarding without leaking provider refs."""

    onboarding_url: str
    onboarding_status: str
    can_accept_sales: bool
    expires_in_seconds: int | None = None


class MarketplacePublicationUpsertRequest(BaseModel):
    """Seller-authored publication payload for marketplace listing management."""

    source_cookbook_id: uuid.UUID
    publication_status: MarketplaceCookbookPublicationStatus = MarketplaceCookbookPublicationStatus.PUBLISHED
    slug: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    subtitle: Optional[str] = Field(default=None, max_length=300)
    description: str = Field(min_length=1, max_length=4000)
    cover_image_url: Optional[str] = Field(default=None, max_length=500)
    list_price_cents: int = Field(ge=0)
    currency: str = Field(default="usd", min_length=3, max_length=3)
    publication_notes: Optional[str] = Field(default=None, max_length=500)


class MarketplacePublicationListResponse(BaseModel):
    items: list[MarketplaceCookbookPublicationSummary] = Field(default_factory=list)


class CookbookRevenueShare(BaseModel):
    """Backend-authored, provider-safe revenue split metadata."""

    seller_share_cents: int = Field(ge=0)
    platform_share_cents: int = Field(ge=0)
    seller_share_ratio: str
    platform_share_ratio: str
    list_price_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class MarketplaceCheckoutResponse(BaseModel):
    """Buyer-facing checkout creation response for one marketplace cookbook."""

    checkout_url: str
    checkout_status: str
    catalog_cookbook_id: uuid.UUID
    marketplace_cookbook_publication_id: uuid.UUID
    revenue_share: CookbookRevenueShare


class MarketplacePurchaseCompletionRequest(BaseModel):
    """Backend-authored sale completion payload, fed by provider/webhook-safe metadata."""

    marketplace_cookbook_publication_id: uuid.UUID
    provider_checkout_ref: str | None = Field(default=None, max_length=255)
    provider_completion_ref: str = Field(min_length=1, max_length=255)
    checkout_status: str = Field(min_length=1, max_length=40)
    provider: str = Field(default="stripe", min_length=1, max_length=50)


class MarketplacePurchaseCompletionResponse(BaseModel):
    """Completion outcome without exposing provider or transfer internals."""

    checkout_status: str
    purchase_state: str
    ownership_granted: bool
    ownership_recorded: bool
    replayed_completion: bool
    catalog_cookbook_id: uuid.UUID
    marketplace_cookbook_publication_id: uuid.UUID
    sale_diagnostics: dict[str, str | int | bool | None] = Field(default_factory=dict)
