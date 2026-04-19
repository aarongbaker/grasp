from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, select

from app.models.authored_recipe import RecipeCookbookRecord
from app.models.session import Session
from app.models.user import (
    CatalogCookbookOwnershipRecord,
    CatalogCookbookPurchaseRecord,
    CatalogPurchaseProvider,
    CatalogPurchaseState,
    EntitlementKind,
    MarketplaceCookbookPublicationRecord,
    MarketplaceCookbookPublicationStatus,
    SellerPayoutAccountRecord,
    SellerPayoutOnboardingStatus,
    SubscriptionSnapshot,
    SubscriptionStatus,
    SubscriptionSyncState,
    UserEntitlementGrant,
    UserProfile,
)
from app.services.access import AccessResolverInput, derive_catalog_cookbook_access
from app.services.catalog_purchases import CatalogPurchaseService, UnknownCatalogCookbookError
from app.services.marketplace_publications import (
    MarketplacePublicationOwnershipError,
    assert_source_cookbook_owned_by_chef,
    build_marketplace_publication_view,
    get_marketplace_publication_by_source,
    get_seller_payout_account,
)
from tests.conftest import _ensure_test_postgres_available, register_test_sqlmodel_metadata, reset_test_database


KNOWN_CATALOG_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


@pytest.fixture
async def catalog_purchase_db():
    _ensure_test_postgres_available()
    from app.core.settings import get_settings

    register_test_sqlmodel_metadata()
    settings = get_settings()
    engine = create_async_engine(settings.test_database_url, echo=False, future=True, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, checkfirst=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await reset_test_database(session)
        yield session
        await session.rollback()

    await engine.dispose()


@pytest.fixture
async def catalog_purchase_user(catalog_purchase_db: AsyncSession) -> UserProfile:
    user_id = uuid.uuid4()
    email = f"catalog-chef-{user_id}@test.com"
    user = UserProfile(
        user_id=user_id,
        name="Catalog Chef",
        email=email,
        rag_owner_key=UserProfile.build_rag_owner_key(email),
    )
    catalog_purchase_db.add(user)
    await catalog_purchase_db.flush()
    persisted_user = await catalog_purchase_db.get(UserProfile, user_id)
    if persisted_user is None:
        raise AssertionError("expected persisted catalog purchase user fixture")
    return persisted_user


def _service(now: datetime | None = None) -> CatalogPurchaseService:
    now_value = now or datetime(2026, 4, 14, 22, 30, 0)
    return CatalogPurchaseService(
        known_catalog_cookbook_ids={KNOWN_CATALOG_ID},
        now_fn=lambda: now_value,
    )


@pytest.mark.asyncio
async def test_record_successful_purchase_creates_exactly_one_purchase_and_ownership(catalog_purchase_db, catalog_purchase_user):
    service = _service()

    first = await service.record_successful_purchase(
        catalog_purchase_db,
        user_id=catalog_purchase_user.user_id,
        catalog_cookbook_id=KNOWN_CATALOG_ID,
        provider=CatalogPurchaseProvider.STRIPE,
        provider_checkout_ref="cs_test_123",
        provider_completion_ref="evt_checkout_complete_123",
        purchase_metadata={"source": "webhook"},
    )
    await catalog_purchase_db.commit()

    second = await service.record_successful_purchase(
        catalog_purchase_db,
        user_id=catalog_purchase_user.user_id,
        catalog_cookbook_id=KNOWN_CATALOG_ID,
        provider=CatalogPurchaseProvider.STRIPE,
        provider_checkout_ref="cs_test_123",
        provider_completion_ref="evt_checkout_complete_123",
        purchase_metadata={"source": "webhook"},
    )
    await catalog_purchase_db.commit()

    purchases = (await catalog_purchase_db.execute(select(CatalogCookbookPurchaseRecord))).scalars().all()
    ownerships = (await catalog_purchase_db.execute(select(CatalogCookbookOwnershipRecord))).scalars().all()

    assert len(purchases) == 1
    assert len(ownerships) == 1
    assert first.created_purchase_record is True
    assert first.created_ownership_record is True
    assert second.created_purchase_record is False
    assert second.created_ownership_record is False
    assert second.reused_existing_completion is True
    assert first.purchase_record is not None
    assert second.purchase_record is not None
    assert first.purchase_record.catalog_cookbook_purchase_record_id == second.purchase_record.catalog_cookbook_purchase_record_id
    assert first.ownership_record is not None
    assert second.ownership_record is not None
    assert first.ownership_record.catalog_cookbook_ownership_record_id == second.ownership_record.catalog_cookbook_ownership_record_id
    assert purchases[0].purchase_state == CatalogPurchaseState.COMPLETED
    assert purchases[0].provider_completion_ref == "evt_checkout_complete_123"
    assert ownerships[0].catalog_cookbook_id == KNOWN_CATALOG_ID


@pytest.mark.asyncio
async def test_record_successful_purchase_refuses_unknown_catalog_ids(catalog_purchase_db, catalog_purchase_user):
    service = _service()

    with pytest.raises(UnknownCatalogCookbookError, match="Unknown catalog cookbook"):
        await service.record_successful_purchase(
            catalog_purchase_db,
            user_id=catalog_purchase_user.user_id,
            catalog_cookbook_id=uuid.uuid4(),
            provider_completion_ref="evt_unknown_catalog",
            provider_checkout_ref="cs_unknown",
        )


@pytest.mark.asyncio
async def test_record_non_completed_purchase_skips_ownership_for_failed_and_cancelled(catalog_purchase_db, catalog_purchase_user):
    service = _service()

    failed = await service.record_non_completed_purchase(
        catalog_purchase_db,
        user_id=catalog_purchase_user.user_id,
        catalog_cookbook_id=KNOWN_CATALOG_ID,
        state=CatalogPurchaseState.FAILED,
        provider=CatalogPurchaseProvider.STRIPE,
        provider_checkout_ref="cs_failed",
        provider_completion_ref="evt_failed",
        failure_code="payment_failed",
        failure_message="Card was declined",
        purchase_metadata={"source": "webhook"},
    )
    cancelled = await service.record_non_completed_purchase(
        catalog_purchase_db,
        user_id=catalog_purchase_user.user_id,
        catalog_cookbook_id=KNOWN_CATALOG_ID,
        state=CatalogPurchaseState.CANCELLED,
        provider=CatalogPurchaseProvider.STRIPE,
        provider_checkout_ref="cs_cancelled",
        provider_completion_ref="evt_cancelled",
        purchase_metadata={"source": "checkout"},
    )
    await catalog_purchase_db.commit()

    purchases = (await catalog_purchase_db.execute(select(CatalogCookbookPurchaseRecord))).scalars().all()
    ownerships = (await catalog_purchase_db.execute(select(CatalogCookbookOwnershipRecord))).scalars().all()

    assert len(purchases) == 2
    assert ownerships == []
    assert failed.decision.should_record_completion is False
    assert failed.ownership_record is None
    assert failed.purchase_record is not None
    assert failed.purchase_record.purchase_state == CatalogPurchaseState.FAILED
    assert failed.purchase_record.failure_code == "payment_failed"
    assert cancelled.decision.should_record_completion is False
    assert cancelled.ownership_record is None
    assert cancelled.purchase_record is not None
    assert cancelled.purchase_record.purchase_state == CatalogPurchaseState.CANCELLED


@pytest.mark.asyncio
async def test_durable_purchase_ownership_survives_subscription_changes_in_access_resolution(catalog_purchase_db, catalog_purchase_user):
    service = _service()
    await service.record_successful_purchase(
        catalog_purchase_db,
        user_id=catalog_purchase_user.user_id,
        catalog_cookbook_id=KNOWN_CATALOG_ID,
        provider=CatalogPurchaseProvider.STRIPE,
        provider_checkout_ref="cs_owned",
        provider_completion_ref="evt_owned",
        purchase_metadata={"source": "webhook"},
    )
    catalog_purchase_db.add(
        SubscriptionSnapshot(
            subscription_snapshot_id=uuid.uuid4(),
            user_id=catalog_purchase_user.user_id,
            provider="stripe",
            status=SubscriptionStatus.CANCELLED,
            sync_state=SubscriptionSyncState.SYNCED,
        )
    )
    catalog_purchase_db.add(
        UserEntitlementGrant(
            entitlement_grant_id=uuid.uuid4(),
            user_id=catalog_purchase_user.user_id,
            kind=EntitlementKind.CATALOG_PREMIUM,
            source="stripe",
            is_active=False,
        )
    )
    await catalog_purchase_db.commit()

    has_owned = await service.has_owned_catalog_cookbook(
        catalog_purchase_db,
        user_id=catalog_purchase_user.user_id,
        catalog_cookbook_id=KNOWN_CATALOG_ID,
    )
    derived = derive_catalog_cookbook_access(
        AccessResolverInput(
            user_id=catalog_purchase_user.user_id,
            catalog_cookbook_id=KNOWN_CATALOG_ID,
            audience="premium",
            has_premium_entitlement=False,
            has_durable_purchase_ownership=has_owned,
            subscription_status=SubscriptionStatus.CANCELLED,
            sync_state=SubscriptionSyncState.SYNCED,
            diagnostics=None,
        )
    )

    assert has_owned is True
    assert derived.access_state.value == "included"
    assert derived.access_state_reason == "Previously purchased cookbook access is included"


@pytest.mark.asyncio
async def test_existing_ownership_is_reused_when_another_completion_targets_same_cookbook(catalog_purchase_db, catalog_purchase_user):
    service = _service()

    first = await service.record_successful_purchase(
        catalog_purchase_db,
        user_id=catalog_purchase_user.user_id,
        catalog_cookbook_id=KNOWN_CATALOG_ID,
        provider_completion_ref="evt_first_completion",
        provider_checkout_ref="cs_first",
    )
    await catalog_purchase_db.commit()

    second = await service.record_successful_purchase(
        catalog_purchase_db,
        user_id=catalog_purchase_user.user_id,
        catalog_cookbook_id=KNOWN_CATALOG_ID,
        provider_completion_ref="evt_second_completion",
        provider_checkout_ref="cs_second",
    )
    await catalog_purchase_db.commit()

    purchases = (await catalog_purchase_db.execute(select(CatalogCookbookPurchaseRecord).order_by(CatalogCookbookPurchaseRecord.created_at))).scalars().all()
    ownerships = (await catalog_purchase_db.execute(select(CatalogCookbookOwnershipRecord))).scalars().all()

    assert len(purchases) == 2
    assert len(ownerships) == 1
    assert first.ownership_record is not None
    assert second.ownership_record is not None
    assert first.ownership_record.catalog_cookbook_ownership_record_id == second.ownership_record.catalog_cookbook_ownership_record_id
    assert second.created_purchase_record is True
    assert second.created_ownership_record is False
    assert second.reused_existing_completion is False


@pytest.mark.asyncio
async def test_seller_payout_account_snapshot_round_trips_without_exposing_provider_contracts(catalog_purchase_db, catalog_purchase_user):
    payout = SellerPayoutAccountRecord(
        user_id=catalog_purchase_user.user_id,
        onboarding_status=SellerPayoutOnboardingStatus.INCOMPLETE,
        charges_enabled=False,
        payouts_enabled=False,
        details_submitted=True,
        provider_account_ref="acct_private_123",
        requirements_due=["external_account", "verification_document"],
        status_reason="Additional verification is required before payouts can be enabled.",
        provider_snapshot={"capabilities": {"transfers": "pending"}},
    )
    catalog_purchase_db.add(payout)
    await catalog_purchase_db.commit()

    stored = await get_seller_payout_account(
        catalog_purchase_db,
        user_id=catalog_purchase_user.user_id,
    )

    assert stored is not None
    assert stored.onboarding_status == SellerPayoutOnboardingStatus.INCOMPLETE
    assert stored.requirements_due == ["external_account", "verification_document"]
    assert stored.status_reason == "Additional verification is required before payouts can be enabled."
    assert stored.provider_account_ref == "acct_private_123"
    assert stored.provider_snapshot == {"capabilities": {"transfers": "pending"}}


@pytest.mark.asyncio
async def test_marketplace_publication_requires_source_cookbook_owned_by_same_chef(catalog_purchase_db, catalog_purchase_user):
    foreign_email = f"foreign-chef-{uuid.uuid4()}@test.com"
    foreign_user = UserProfile(
        user_id=uuid.uuid4(),
        name="Foreign Chef",
        email=foreign_email,
        rag_owner_key=UserProfile.build_rag_owner_key(foreign_email),
    )
    catalog_purchase_db.add(foreign_user)
    await catalog_purchase_db.commit()

    foreign_cookbook = RecipeCookbookRecord(
        user_id=foreign_user.user_id,
        name="Foreign Private Menu",
        description="Not publishable by another chef.",
    )
    catalog_purchase_db.add(foreign_cookbook)
    await catalog_purchase_db.commit()
    await catalog_purchase_db.refresh(foreign_cookbook)

    with pytest.raises(
        MarketplacePublicationOwnershipError,
        match="owned by the publishing chef",
    ):
        await assert_source_cookbook_owned_by_chef(
            catalog_purchase_db,
            chef_user_id=catalog_purchase_user.user_id,
            source_cookbook_id=foreign_cookbook.cookbook_id,
        )


@pytest.mark.asyncio
async def test_marketplace_publication_round_trips_separately_from_purchase_ownership_tables(catalog_purchase_db, catalog_purchase_user):
    source_cookbook = RecipeCookbookRecord(
        user_id=catalog_purchase_user.user_id,
        name="Chef Spring Collection",
        description="Private authored cookbook source for marketplace publication.",
    )
    catalog_purchase_db.add(source_cookbook)
    await catalog_purchase_db.commit()
    await catalog_purchase_db.refresh(source_cookbook)

    owned_source = await assert_source_cookbook_owned_by_chef(
        catalog_purchase_db,
        chef_user_id=catalog_purchase_user.user_id,
        source_cookbook_id=source_cookbook.cookbook_id,
    )
    assert owned_source.cookbook_id == source_cookbook.cookbook_id

    publication = MarketplaceCookbookPublicationRecord(
        chef_user_id=catalog_purchase_user.user_id,
        source_cookbook_id=source_cookbook.cookbook_id,
        publication_status=MarketplaceCookbookPublicationStatus.PUBLISHED,
        title="Spring Marketplace Collection",
        subtitle="Provider-safe public listing",
        description="Derived from the chef's private authored cookbook for marketplace sale.",
        slug="spring-marketplace-collection",
        list_price_cents=2400,
        currency="usd",
        recipe_count_snapshot=7,
        publication_notes="Assumption: one source cookbook maps to one publication row.",
        publication_metadata={"source_visibility": "private cookbook retained"},
        published_at=datetime(2026, 4, 15, 8, 0, 0),
    )
    catalog_purchase_db.add(publication)
    await catalog_purchase_db.commit()

    stored = await get_marketplace_publication_by_source(
        catalog_purchase_db,
        chef_user_id=catalog_purchase_user.user_id,
        source_cookbook_id=source_cookbook.cookbook_id,
    )
    purchases = (await catalog_purchase_db.execute(select(CatalogCookbookPurchaseRecord))).scalars().all()
    ownerships = (await catalog_purchase_db.execute(select(CatalogCookbookOwnershipRecord))).scalars().all()

    assert stored is not None
    assert stored.publication_status == MarketplaceCookbookPublicationStatus.PUBLISHED
    assert stored.source_cookbook_id == source_cookbook.cookbook_id
    assert stored.chef_user_id == catalog_purchase_user.user_id
    assert stored.list_price_cents == 2400
    assert stored.recipe_count_snapshot == 7
    assert purchases == []
    assert ownerships == []

    contract = build_marketplace_publication_view(stored)
    assert contract.marketplace_cookbook_publication_id == stored.marketplace_cookbook_publication_id
    assert contract.source_cookbook_id == source_cookbook.cookbook_id
    assert contract.publication_status == MarketplaceCookbookPublicationStatus.PUBLISHED
    assert contract.slug == "spring-marketplace-collection"
    assert contract.published_at == "2026-04-15T08:00:00"
    assert "provider_account_ref" not in contract.model_dump_json()


@pytest.mark.asyncio
async def test_marketplace_publication_uniqueness_prevents_duplicate_rows_for_same_chef_and_source(catalog_purchase_db, catalog_purchase_user):
    source_cookbook = RecipeCookbookRecord(
        user_id=catalog_purchase_user.user_id,
        name="One Source Cookbook",
        description="Source cookbook for uniqueness check.",
    )
    catalog_purchase_db.add(source_cookbook)
    await catalog_purchase_db.commit()
    await catalog_purchase_db.refresh(source_cookbook)

    first = MarketplaceCookbookPublicationRecord(
        chef_user_id=catalog_purchase_user.user_id,
        source_cookbook_id=source_cookbook.cookbook_id,
        publication_status=MarketplaceCookbookPublicationStatus.DRAFT,
        title="First Listing",
        description="First publication row for this private cookbook.",
        slug="first-listing",
        list_price_cents=1500,
        currency="usd",
        recipe_count_snapshot=3,
    )
    duplicate = MarketplaceCookbookPublicationRecord(
        chef_user_id=catalog_purchase_user.user_id,
        source_cookbook_id=source_cookbook.cookbook_id,
        publication_status=MarketplaceCookbookPublicationStatus.DRAFT,
        title="Duplicate Listing",
        description="Should fail because the chef/source pair is already published once.",
        slug="duplicate-listing",
        list_price_cents=1800,
        currency="usd",
        recipe_count_snapshot=4,
    )
    catalog_purchase_db.add(first)
    await catalog_purchase_db.commit()

    catalog_purchase_db.add(duplicate)
    with pytest.raises(Exception):
        await catalog_purchase_db.commit()
    await catalog_purchase_db.rollback()

    publications = (await catalog_purchase_db.execute(select(MarketplaceCookbookPublicationRecord))).scalars().all()
    assert len(publications) == 1
    assert publications[0].title == "First Listing"
