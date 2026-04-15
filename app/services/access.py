"""App-level cookbook access resolution.

The resolver in this module is the authoritative boundary between persisted
subscription/entitlement state and consumer-facing catalog/planner contracts.
It emits only canonical application decisions and diagnostics — never raw
billing-provider payloads.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

from app.models.catalog import CatalogCookbookAccessState, CatalogCookbookAudience
from app.models.user import SubscriptionStatus, SubscriptionSyncState
from app.services.catalog_purchases import CatalogPurchaseService
from app.services.subscriptions import SubscriptionDiagnostics


class AccessEntitlement(str, Enum):
    """Normalized app-level capabilities for catalog access decisions."""

    CATALOG_PREVIEW = "catalog_preview"
    CATALOG_PREMIUM = "catalog_premium"


@dataclass(frozen=True)
class AccessResolverInput:
    """Normalized subscription/access inputs independent of route models."""

    user_id: uuid.UUID | None
    audience: CatalogCookbookAudience
    catalog_cookbook_id: uuid.UUID | None = None
    has_preview_entitlement: bool = False
    has_premium_entitlement: bool = False
    has_durable_purchase_ownership: bool = False
    subscription_status: SubscriptionStatus | None = None
    sync_state: SubscriptionSyncState | None = None
    diagnostics: SubscriptionDiagnostics | None = None


@dataclass(frozen=True)
class DerivedCookbookAccess:
    """Authoritative app-level cookbook access decision plus diagnostics."""

    access_state: CatalogCookbookAccessState
    access_state_reason: str
    diagnostics: SubscriptionDiagnostics | None = None


def derive_catalog_cookbook_access(input: AccessResolverInput) -> DerivedCookbookAccess:
    """Resolve one canonical included/preview/locked cookbook access state."""

    if input.audience == CatalogCookbookAudience.INCLUDED:
        return DerivedCookbookAccess(
            access_state=CatalogCookbookAccessState.INCLUDED,
            access_state_reason="Included with the base catalog",
            diagnostics=input.diagnostics,
        )

    if input.audience == CatalogCookbookAudience.PREVIEW:
        return DerivedCookbookAccess(
            access_state=CatalogCookbookAccessState.PREVIEW,
            access_state_reason="Preview access enabled for this chef",
            diagnostics=input.diagnostics,
        )

    if input.has_durable_purchase_ownership:
        return DerivedCookbookAccess(
            access_state=CatalogCookbookAccessState.INCLUDED,
            access_state_reason="Previously purchased cookbook access is included",
            diagnostics=input.diagnostics,
        )

    if input.has_premium_entitlement:
        return DerivedCookbookAccess(
            access_state=CatalogCookbookAccessState.INCLUDED,
            access_state_reason="Premium catalog access enabled",
            diagnostics=input.diagnostics,
        )

    if input.subscription_status in {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.TRIALING,
        SubscriptionStatus.GRACE_PERIOD,
    }:
        return DerivedCookbookAccess(
            access_state=CatalogCookbookAccessState.LOCKED,
            access_state_reason="Current subscription does not include this catalog cookbook",
            diagnostics=input.diagnostics,
        )

    if input.sync_state == SubscriptionSyncState.FAILED:
        return DerivedCookbookAccess(
            access_state=CatalogCookbookAccessState.LOCKED,
            access_state_reason="Catalog access unavailable until subscription sync recovers",
            diagnostics=input.diagnostics,
        )

    return DerivedCookbookAccess(
        access_state=CatalogCookbookAccessState.LOCKED,
        access_state_reason="Upgrade required for this catalog cookbook",
        diagnostics=input.diagnostics,
    )
