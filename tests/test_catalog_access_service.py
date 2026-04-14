"""Focused tests for the provider-agnostic cookbook access resolver."""

import uuid

from app.models.catalog import CatalogCookbookAccessState, CatalogCookbookAudience
from app.models.user import SubscriptionStatus, SubscriptionSyncState
from app.services.access import AccessResolverInput, derive_catalog_cookbook_access
from app.services.subscriptions import SubscriptionDiagnostics


def _diagnostics() -> SubscriptionDiagnostics:
    return SubscriptionDiagnostics(
        subscription_snapshot_id=uuid.uuid4(),
        subscription_status=SubscriptionStatus.ACTIVE,
        sync_state=SubscriptionSyncState.SYNCED,
        provider="stripe",
    )


def test_derive_catalog_cookbook_access_returns_included_for_base_catalog_without_subscription_dependency():
    result = derive_catalog_cookbook_access(
        AccessResolverInput(
            user_id=uuid.uuid4(),
            audience=CatalogCookbookAudience.INCLUDED,
            diagnostics=_diagnostics(),
        )
    )

    assert result.access_state == CatalogCookbookAccessState.INCLUDED
    assert result.access_state_reason == "Included with the base catalog"
    assert result.diagnostics is not None


def test_derive_catalog_cookbook_access_returns_preview_for_preview_audience_without_profile_defaults():
    result = derive_catalog_cookbook_access(
        AccessResolverInput(
            user_id=uuid.uuid4(),
            audience=CatalogCookbookAudience.PREVIEW,
        )
    )

    assert result.access_state == CatalogCookbookAccessState.PREVIEW
    assert result.access_state_reason == "Preview access enabled for this chef"


def test_derive_catalog_cookbook_access_unlocks_premium_when_explicit_entitlement_present():
    result = derive_catalog_cookbook_access(
        AccessResolverInput(
            user_id=uuid.uuid4(),
            audience=CatalogCookbookAudience.PREMIUM,
            has_premium_entitlement=True,
            subscription_status=SubscriptionStatus.ACTIVE,
            sync_state=SubscriptionSyncState.SYNCED,
        )
    )

    assert result.access_state == CatalogCookbookAccessState.INCLUDED
    assert result.access_state_reason == "Premium catalog access enabled"


def test_derive_catalog_cookbook_access_keeps_future_tier_room_without_raw_provider_payloads():
    result = derive_catalog_cookbook_access(
        AccessResolverInput(
            user_id=uuid.uuid4(),
            audience=CatalogCookbookAudience.PREMIUM,
            has_premium_entitlement=False,
            subscription_status=SubscriptionStatus.ACTIVE,
            sync_state=SubscriptionSyncState.SYNCED,
            diagnostics=_diagnostics(),
        )
    )

    assert result.access_state == CatalogCookbookAccessState.LOCKED
    assert result.access_state_reason == "Current subscription does not include this catalog cookbook"
    assert result.diagnostics is not None
    assert result.diagnostics.provider == "stripe"


def test_derive_catalog_cookbook_access_surfaces_sync_failure_for_observability():
    result = derive_catalog_cookbook_access(
        AccessResolverInput(
            user_id=uuid.uuid4(),
            audience=CatalogCookbookAudience.PREMIUM,
            sync_state=SubscriptionSyncState.FAILED,
        )
    )

    assert result.access_state == CatalogCookbookAccessState.LOCKED
    assert result.access_state_reason == "Catalog access unavailable until subscription sync recovers"
