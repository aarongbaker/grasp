from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from app.models.user import SubscriptionSnapshot, SubscriptionStatus, SubscriptionSyncState, UserProfile
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

    async def create_customer(self, **kwargs):  # pragma: no cover - not used here
        raise NotImplementedError

    async def create_checkout_session(self, **kwargs):  # pragma: no cover - not used here
        raise NotImplementedError

    async def create_billing_portal_session(self, **kwargs):  # pragma: no cover - not used here
        raise NotImplementedError

    async def retrieve_customer(self, customer_id: str):  # pragma: no cover - not used here
        return {"id": customer_id}


@pytest.fixture
async def stripe_service_db():
    _ensure_test_postgres_available()
    from app.core.settings import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.test_database_url, echo=False, future=True, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
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
