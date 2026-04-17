from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from app.models.catalog import MarketplaceCookbookPublicationStatus
from app.models.session import Session
from app.models.user import (
    MarketplaceCookbookPublicationRecord,
    SellerPayoutAccountRecord,
    SellerPayoutOnboardingStatus,
    SubscriptionSnapshot,
    SubscriptionStatus,
    SubscriptionSyncState,
    UserProfile,
)
from app.services.generation_billing import GenerationBillingService
from app.services.stripe_billing import StripeBillingService, StripeWebhookPayloadError
from tests.conftest import _ensure_test_postgres_available


class _GatewayStub:
    async def retrieve_subscription(self, subscription_id: str):
        return {
            "id": subscription_id,
            "status": "active",
            "current_period_end": None,
            "items": {"data": [{"price": {"id": "price_test_catalog"}}]},
        }

    async def create_customer(self, **kwargs):
        return {"id": "cus_test_generated"}

    async def create_checkout_session(self, **kwargs):  # pragma: no cover - not used here
        raise NotImplementedError

    async def create_billing_portal_session(self, **kwargs):
        return {"url": "https://billing.stripe.test/portal_session"}

    async def retrieve_customer(self, customer_id: str):  # pragma: no cover - not used here
        return {"id": customer_id}


@pytest.fixture
async def stripe_service_db():
    _ensure_test_postgres_available()
    from app.core.settings import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.test_database_url, echo=False, future=True, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with session_factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()


def _settings_stub() -> SimpleNamespace:
    return SimpleNamespace(
        stripe_secret_key="sk_test_123",
        stripe_webhook_secret="whsec_test_123",
        stripe_webhook_tolerance_seconds=300,
        stripe_price_id="price_test_catalog",
        stripe_checkout_success_url="http://localhost/success",
        stripe_checkout_cancel_url="http://localhost/cancel",
        stripe_portal_return_url="http://localhost/account",
    )


def _service() -> StripeBillingService:
    return StripeBillingService(settings=_settings_stub(), gateway=_GatewayStub())


@pytest.mark.asyncio
async def test_subscription_webhook_falls_back_to_existing_subscription_snapshot_linkage(stripe_service_db):
    service = _service()
    user = UserProfile(
        user_id=uuid.uuid4(),
        name="Chef Linkage",
        email="chef-linkage@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("chef-linkage@test.com"),
    )
    snapshot = SubscriptionSnapshot(
        subscription_snapshot_id=uuid.uuid4(),
        user_id=user.user_id,
        provider="stripe",
        provider_customer_ref="cus_existing",
        provider_subscription_ref="sub_existing",
        status=SubscriptionStatus.CANCELLED,
        sync_state=SubscriptionSyncState.PENDING,
    )
    stripe_service_db.add(user)
    stripe_service_db.add(snapshot)
    await stripe_service_db.commit()

    resolved_user_id = await service._resolve_subscription_webhook_user_id(
        stripe_service_db,
        subscription_id="sub_existing",
        customer_id="cus_existing",
        metadata_user_id=None,
    )

    assert resolved_user_id == user.user_id


@pytest.mark.asyncio
async def test_subscription_webhook_falls_back_to_existing_customer_snapshot_linkage(stripe_service_db):
    service = _service()
    user = UserProfile(
        user_id=uuid.uuid4(),
        name="Chef Customer Linkage",
        email="chef-customer@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("chef-customer@test.com"),
    )
    snapshot = SubscriptionSnapshot(
        subscription_snapshot_id=uuid.uuid4(),
        user_id=user.user_id,
        provider="stripe",
        provider_customer_ref="cus_customer_only",
        provider_subscription_ref=None,
        status=SubscriptionStatus.CANCELLED,
        sync_state=SubscriptionSyncState.PENDING,
    )
    stripe_service_db.add(user)
    stripe_service_db.add(snapshot)
    await stripe_service_db.commit()

    resolved_user_id = await service._resolve_subscription_webhook_user_id(
        stripe_service_db,
        subscription_id="sub_new_from_stripe",
        customer_id="cus_customer_only",
        metadata_user_id=None,
    )

    assert resolved_user_id == user.user_id


@pytest.mark.asyncio
async def test_subscription_webhook_rejects_invalid_metadata_user_id_before_fallback(stripe_service_db):
    service = _service()

    with pytest.raises(StripeWebhookPayloadError, match="metadata user_id is invalid"):
        await service._resolve_subscription_webhook_user_id(
            stripe_service_db,
            subscription_id="sub_any",
            customer_id="cus_any",
            metadata_user_id="not-a-uuid",
        )


@pytest.mark.asyncio
async def test_create_generation_setup_session_uses_customer_boundary_and_hides_provider_refs(stripe_service_db):
    service = _service()
    user = UserProfile(
        user_id=uuid.uuid4(),
        name="Chef Setup",
        email="chef-setup@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("chef-setup@test.com"),
        generation_payment_method_required=True,
        has_saved_generation_payment_method=False,
    )
    stripe_service_db.add(user)
    await stripe_service_db.commit()

    bundle = await service.create_generation_setup_session(stripe_service_db, user=user)

    assert bundle.url == "https://billing.stripe.test/portal_session"
    assert bundle.customer_state == "created"
    assert bundle.payment_method_status == "missing"
    assert not hasattr(bundle, "provider_customer_ref")


@pytest.mark.asyncio
async def test_seller_payout_readiness_is_provider_safe_and_actionable(stripe_service_db):
    service = _service()
    user = UserProfile(
        user_id=uuid.uuid4(),
        name="Chef Seller",
        email="chef-seller@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("chef-seller@test.com"),
    )
    stripe_service_db.add(user)
    await stripe_service_db.commit()

    readiness = await service.get_or_create_seller_payout_readiness(stripe_service_db, user=user)

    assert readiness.onboarding_status == "not_started"
    assert readiness.can_accept_sales is False
    assert readiness.has_onboarding_action is True
    assert "provider_account_ref" not in repr(readiness)


@pytest.mark.asyncio
async def test_marketplace_revenue_share_is_deterministic_70_30(stripe_service_db):
    service = _service()

    revenue_share = service.build_marketplace_revenue_share(list_price_cents=3200, currency="usd")

    assert revenue_share.seller_share_cents == 2240
    assert revenue_share.platform_share_cents == 960
    assert revenue_share.seller_share_ratio == "70%"
    assert revenue_share.platform_share_ratio == "30%"


@pytest.mark.asyncio
async def test_create_marketplace_checkout_session_returns_provider_safe_revenue_share_diagnostics(stripe_service_db):
    service = _service()
    buyer = UserProfile(
        user_id=uuid.uuid4(),
        name="Buyer Diagnostics",
        email="buyer-diagnostics@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("buyer-diagnostics@test.com"),
    )
    chef = UserProfile(
        user_id=uuid.uuid4(),
        name="Chef Diagnostics",
        email="chef-diagnostics@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("chef-diagnostics@test.com"),
    )
    publication = MarketplaceCookbookPublicationRecord(
        marketplace_cookbook_publication_id=uuid.uuid4(),
        chef_user_id=chef.user_id,
        source_cookbook_id=uuid.uuid4(),
        publication_status=MarketplaceCookbookPublicationStatus.PUBLISHED,
        slug="diagnostic-book",
        title="Diagnostic Book",
        description="For checkout diagnostics.",
        list_price_cents=3000,
        currency="usd",
        recipe_count_snapshot=4,
    )
    stripe_service_db.add(buyer)
    stripe_service_db.add(chef)
    stripe_service_db.add(publication)
    await stripe_service_db.commit()

    bundle = await service.create_marketplace_checkout_session(
        stripe_service_db,
        buyer=buyer,
        publication=publication,
    )

    assert bundle.checkout_status == "requires_payment"
    assert bundle.revenue_share.list_price_cents == 3000
    assert bundle.revenue_share.seller_share_cents == 2100
    assert bundle.revenue_share.platform_share_cents == 900
    assert "transfer" not in repr(bundle).lower()


@pytest.mark.asyncio
async def test_finalize_marketplace_purchase_records_exactly_once_ownership_for_completed_checkout(stripe_service_db):
    service = _service()
    buyer = UserProfile(
        user_id=uuid.uuid4(),
        name="Buyer Chef",
        email="buyer-chef@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("buyer-chef@test.com"),
    )
    chef = UserProfile(
        user_id=uuid.uuid4(),
        name="Seller Chef",
        email=f"seller-chef-{uuid.uuid4()}@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key(f"seller-chef-{uuid.uuid4()}@test.com"),
    )
    publication = MarketplaceCookbookPublicationRecord(
        marketplace_cookbook_publication_id=uuid.uuid4(),
        chef_user_id=chef.user_id,
        source_cookbook_id=uuid.uuid4(),
        publication_status=MarketplaceCookbookPublicationStatus.PUBLISHED,
        slug="seller-book",
        title="Seller Book",
        description="For sale.",
        list_price_cents=3200,
        currency="usd",
        recipe_count_snapshot=4,
    )
    stripe_service_db.add(buyer)
    stripe_service_db.add(chef)
    stripe_service_db.add(publication)
    await stripe_service_db.commit()

    first = await service.finalize_marketplace_purchase(
        stripe_service_db,
        buyer=buyer,
        publication=publication,
        provider_checkout_ref="cs_marketplace_1",
        provider_completion_ref="evt_marketplace_complete_1",
        checkout_status="completed",
    )
    second = await service.finalize_marketplace_purchase(
        stripe_service_db,
        buyer=buyer,
        publication=publication,
        provider_checkout_ref="cs_marketplace_1",
        provider_completion_ref="evt_marketplace_complete_1",
        checkout_status="completed",
    )

    assert first.ownership_granted is True
    assert first.ownership_recorded is True
    assert second.ownership_granted is True
    assert second.ownership_recorded is False
    assert second.replayed_completion is True


@pytest.mark.asyncio
async def test_finalize_marketplace_purchase_does_not_grant_ownership_for_cancelled_checkout(stripe_service_db):
    service = _service()
    buyer = UserProfile(
        user_id=uuid.uuid4(),
        name="Cancelled Buyer",
        email="cancelled-buyer@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("cancelled-buyer@test.com"),
    )
    chef = UserProfile(
        user_id=uuid.uuid4(),
        name="Seller Chef Two",
        email="seller-chef-two@test.com",
        rag_owner_key=UserProfile.build_rag_owner_key("seller-chef-two@test.com"),
    )
    publication = MarketplaceCookbookPublicationRecord(
        marketplace_cookbook_publication_id=uuid.uuid4(),
        chef_user_id=chef.user_id,
        source_cookbook_id=uuid.uuid4(),
        publication_status=MarketplaceCookbookPublicationStatus.PUBLISHED,
        slug="seller-book-two",
        title="Seller Book Two",
        description="For sale too.",
        list_price_cents=2100,
        currency="usd",
        recipe_count_snapshot=5,
    )
    stripe_service_db.add(buyer)
    stripe_service_db.add(chef)
    stripe_service_db.add(publication)
    await stripe_service_db.commit()

    outcome = await service.finalize_marketplace_purchase(
        stripe_service_db,
        buyer=buyer,
        publication=publication,
        provider_checkout_ref="cs_marketplace_cancelled",
        provider_completion_ref="evt_marketplace_cancelled",
        checkout_status="cancelled",
    )

    assert outcome.purchase_state == "cancelled"
    assert outcome.ownership_granted is False
    assert outcome.ownership_recorded is False


