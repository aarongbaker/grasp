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
