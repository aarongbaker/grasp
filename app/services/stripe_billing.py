from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import AsyncSession as SAAsyncSession

from app.models.catalog import CookbookRevenueShare
from app.models.pipeline import SessionOutstandingBalanceSummary
from app.models.user import (
    CatalogPurchaseProvider,
    CatalogPurchaseState,
    EntitlementKind,
    GenerationBillingState,
    MarketplaceCookbookPublicationRecord,
    SellerPayoutAccountRecord,
    SellerPayoutOnboardingStatus,
    SubscriptionSnapshot,
    SubscriptionStatus,
    SubscriptionSyncState,
    UserEntitlementGrant,
    UserProfile,
)
from app.services.catalog_purchases import CatalogPurchaseOutcome, CatalogPurchaseService
from app.services.generation_billing import GenerationBillingService

logger = logging.getLogger(__name__)


def _session_exec(db: AsyncSession | SAAsyncSession, statement):
    exec_method = getattr(db, "exec", None)
    if callable(exec_method):
        return exec_method(statement)
    return db.execute(statement)

STRIPE_PROVIDER = "stripe"
_PREMIUM_PLAN_CODE = "stripe:catalog-premium"
_STRIPE_SIGNATURE_HEADER = "stripe-signature"
_MARKETPLACE_SELLER_SHARE_BPS = 7000
_MARKETPLACE_PLATFORM_SHARE_BPS = 3000
_MARKETPLACE_CATALOG_ID_NAMESPACE = uuid.UUID("4f7e5ab3-7fc4-41de-a34a-2f87c4fe64a6")


class StripeGatewayProtocol(Protocol):
    async def create_customer(self, *, email: str, name: str | None, metadata: dict[str, str]) -> dict[str, Any]: ...

    async def create_checkout_session(
        self,
        *,
        customer: str,
        success_url: str,
        cancel_url: str,
        price_id: str,
        client_reference_id: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]: ...

    async def create_billing_portal_session(self, *, customer: str, return_url: str) -> dict[str, Any]: ...

    async def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]: ...

    async def retrieve_customer(self, customer_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BillingUrlBundle:
    url: str
    snapshot_id: uuid.UUID | None
    subscription_status: str | None
    sync_state: str | None


@dataclass(frozen=True)
class BillingSetupBundle:
    url: str
    customer_state: str
    payment_method_status: str
    session_id: uuid.UUID | None = None


@dataclass(frozen=True)
class BillingRecoveryBundle:
    url: str
    session_id: uuid.UUID
    outstanding_balance: SessionOutstandingBalanceSummary


@dataclass(frozen=True)
class PaymentMethodStatusBundle:
    has_saved_payment_method: bool
    payment_method_label: str | None


@dataclass(frozen=True)
class StripeWebhookEnvelope:
    event_id: str
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SellerPayoutReadinessBundle:
    onboarding_status: str
    can_accept_sales: bool
    charges_enabled: bool
    payouts_enabled: bool
    details_submitted: bool
    requirements_due: list[str]
    status_reason: str | None
    has_onboarding_action: bool


@dataclass(frozen=True)
class SellerPayoutOnboardingBundle:
    onboarding_url: str
    onboarding_status: str
    can_accept_sales: bool
    expires_in_seconds: int | None


@dataclass(frozen=True)
class MarketplaceCheckoutBundle:
    checkout_url: str
    checkout_status: str
    catalog_cookbook_id: uuid.UUID
    marketplace_cookbook_publication_id: uuid.UUID
    revenue_share: CookbookRevenueShare


@dataclass(frozen=True)
class MarketplaceCompletionBundle:
    checkout_status: str
    purchase_state: str
    ownership_granted: bool
    ownership_recorded: bool
    replayed_completion: bool
    catalog_cookbook_id: uuid.UUID
    marketplace_cookbook_publication_id: uuid.UUID
    purchase_outcome: CatalogPurchaseOutcome | None = None


class StripeSignatureError(Exception):
    pass


class StripeReplayError(Exception):
    pass


class StripeWebhookPayloadError(Exception):
    pass


class StripeBillingGateway:
    """Small async wrapper around the Stripe SDK.

    Imported lazily so tests can patch the service without requiring the SDK.
    """

    def __init__(self, api_key: str):
        import stripe

        stripe.api_key = api_key
        self._stripe = stripe

    async def create_customer(self, *, email: str, name: str | None, metadata: dict[str, str]) -> dict[str, Any]:
        customer = await self._stripe.Customer.create_async(email=email, name=name, metadata=metadata)
        return dict(customer)

    async def create_checkout_session(
        self,
        *,
        customer: str,
        success_url: str,
        cancel_url: str,
        price_id: str,
        client_reference_id: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        session = await self._stripe.checkout.Session.create_async(
            mode="subscription",
            customer=customer,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=client_reference_id,
            metadata=metadata,
            allow_promotion_codes=True,
        )
        return dict(session)

    async def create_billing_portal_session(self, *, customer: str, return_url: str) -> dict[str, Any]:
        session = await self._stripe.billing_portal.Session.create_async(customer=customer, return_url=return_url)
        return dict(session)

    async def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]:
        subscription = await self._stripe.Subscription.retrieve_async(subscription_id)
        return dict(subscription)

    async def retrieve_customer(self, customer_id: str) -> dict[str, Any]:
        customer = await self._stripe.Customer.retrieve_async(customer_id)
        return dict(customer)


class StripeBillingService:
    def __init__(
        self,
        *,
        settings,
        gateway: StripeGatewayProtocol | None = None,
        now_fn=None,
    ):
        self._settings = settings
        self._gateway = gateway or StripeBillingGateway(settings.stripe_secret_key)
        self._now_fn = now_fn or _utcnow

    async def create_checkout_session(self, db: AsyncSession, *, user: UserProfile) -> BillingUrlBundle:
        customer_ref = await self._ensure_customer_ref(db, user=user)
        checkout = await self._gateway.create_checkout_session(
            customer=customer_ref,
            success_url=self._settings.stripe_checkout_success_url,
            cancel_url=self._settings.stripe_checkout_cancel_url,
            price_id=self._settings.stripe_price_id,
            client_reference_id=str(user.user_id),
            metadata={"user_id": str(user.user_id)},
        )
        snapshot = await self._upsert_snapshot(
            db,
            user_id=user.user_id,
            provider_customer_ref=customer_ref,
            provider_subscription_ref=None,
            status=SubscriptionStatus.CANCELLED,
            sync_state=SubscriptionSyncState.PENDING,
            plan_code=_PREMIUM_PLAN_CODE,
            error_code=None,
            error_message=None,
        )
        await db.commit()
        return BillingUrlBundle(
            url=checkout["url"],
            snapshot_id=snapshot.subscription_snapshot_id,
            subscription_status=snapshot.status.value,
            sync_state=snapshot.sync_state.value,
        )

    async def create_portal_session(self, db: AsyncSession, *, user: UserProfile) -> BillingUrlBundle:
        customer_ref = await self._ensure_customer_ref(db, user=user)
        portal = await self._gateway.create_billing_portal_session(
            customer=customer_ref,
            return_url=self._settings.stripe_portal_return_url,
        )
        snapshot = await self._get_latest_snapshot(db, user_id=user.user_id)
        return BillingUrlBundle(
            url=portal["url"],
            snapshot_id=snapshot.subscription_snapshot_id if snapshot else None,
            subscription_status=snapshot.status.value if snapshot else None,
            sync_state=snapshot.sync_state.value if snapshot else None,
        )

    async def get_or_create_seller_payout_readiness(self, db: AsyncSession, *, user: UserProfile) -> SellerPayoutReadinessBundle:
        record = await self._get_or_create_seller_payout_account(db, user=user)
        return self._build_seller_payout_readiness(record)

    async def create_seller_payout_onboarding_session(
        self,
        db: AsyncSession,
        *,
        user: UserProfile,
    ) -> SellerPayoutOnboardingBundle:
        record = await self._get_or_create_seller_payout_account(db, user=user)
        now = self._now_fn()
        if record.onboarding_status == SellerPayoutOnboardingStatus.NOT_STARTED:
            record.onboarding_status = SellerPayoutOnboardingStatus.INCOMPLETE
        record.status_reason = record.status_reason or "Complete payout onboarding to publish and sell cookbooks."
        record.updated_at = now
        if not record.provider_account_ref:
            record.provider_account_ref = f"acct_{user.user_id.hex[:16]}"
        db.add(record)
        await db.commit()
        return SellerPayoutOnboardingBundle(
            onboarding_url=f"{self._settings.stripe_portal_return_url.rstrip('/')}/seller/onboarding",
            onboarding_status=record.onboarding_status.value,
            can_accept_sales=record.charges_enabled and record.payouts_enabled,
            expires_in_seconds=1800,
        )

    async def create_generation_setup_session(
        self,
        db: AsyncSession,
        *,
        user: UserProfile,
        session_id: uuid.UUID | None = None,
    ) -> BillingSetupBundle:
        had_customer_ref = bool(user.stripe_customer_id)
        customer_ref = await self._ensure_customer_ref(db, user=user)
        portal = await self._gateway.create_billing_portal_session(
            customer=customer_ref,
            return_url=self._settings.stripe_portal_return_url,
        )
        customer_state = "existing" if had_customer_ref else "created"
        return BillingSetupBundle(
            url=portal["url"],
            customer_state=customer_state,
            payment_method_status="saved" if user.has_saved_generation_payment_method else "missing",
            session_id=session_id,
        )

    async def create_generation_recovery_session(
        self,
        db: AsyncSession,
        *,
        user: UserProfile,
        session_id: uuid.UUID,
    ) -> BillingRecoveryBundle:
        session = await self._require_owned_session(db, session_id=session_id, user=user)
        customer_ref = await self._ensure_customer_ref(db, user=user)
        portal = await self._gateway.create_billing_portal_session(
            customer=customer_ref,
            return_url=self._settings.stripe_portal_return_url,
        )
        outstanding = await self._build_outstanding_balance_summary(db, session=session)
        return BillingRecoveryBundle(url=portal["url"], session_id=session_id, outstanding_balance=outstanding)

    async def get_generation_payment_method_status(self, db: AsyncSession, *, user: UserProfile) -> PaymentMethodStatusBundle:
        return PaymentMethodStatusBundle(
            has_saved_payment_method=user.has_saved_generation_payment_method,
            payment_method_label=user.default_generation_payment_method_label,
        )

    async def mark_generation_payment_method_ready(
        self,
        db: AsyncSession,
        *,
        user: UserProfile,
        label: str | None = None,
    ) -> PaymentMethodStatusBundle:
        user.has_saved_generation_payment_method = True
        user.default_generation_payment_method_label = (label or user.default_generation_payment_method_label or "Saved card")[:120]
        db.add(user)
        await db.commit()
        return await self.get_generation_payment_method_status(db, user=user)

    async def get_generation_recovery_status(
        self,
        db: AsyncSession,
        *,
        user: UserProfile,
        session_id: uuid.UUID,
    ) -> SessionOutstandingBalanceSummary:
        session = await self._require_owned_session(db, session_id=session_id, user=user)
        return await self._build_outstanding_balance_summary(db, session=session)

    async def create_marketplace_checkout_session(
        self,
        db: AsyncSession,
        *,
        buyer: UserProfile,
        publication: MarketplaceCookbookPublicationRecord,
    ) -> MarketplaceCheckoutBundle:
        revenue_share = self.build_marketplace_revenue_share(
            list_price_cents=publication.list_price_cents,
            currency=publication.currency,
        )
        return MarketplaceCheckoutBundle(
            checkout_url=f"{self._settings.stripe_checkout_success_url.rstrip('/')}/marketplace/{publication.marketplace_cookbook_publication_id}",
            checkout_status="requires_payment",
            catalog_cookbook_id=self.catalog_cookbook_id_for_publication(publication.marketplace_cookbook_publication_id),
            marketplace_cookbook_publication_id=publication.marketplace_cookbook_publication_id,
            revenue_share=revenue_share,
        )

    async def finalize_marketplace_purchase(
        self,
        db: AsyncSession,
        *,
        buyer: UserProfile,
        publication: MarketplaceCookbookPublicationRecord,
        provider_checkout_ref: str | None,
        provider_completion_ref: str,
        checkout_status: str,
        provider: str = STRIPE_PROVIDER,
    ) -> MarketplaceCompletionBundle:
        catalog_cookbook_id = self.catalog_cookbook_id_for_publication(publication.marketplace_cookbook_publication_id)
        purchase_service = CatalogPurchaseService(known_catalog_cookbook_ids={catalog_cookbook_id})
        normalized_status = checkout_status.strip().lower()
        purchase_metadata = {
            "marketplace_cookbook_publication_id": str(publication.marketplace_cookbook_publication_id),
            "source_cookbook_id": str(publication.source_cookbook_id),
            "chef_user_id": str(publication.chef_user_id),
            "publication_status": publication.publication_status.value,
            "revenue_share": self.build_marketplace_revenue_share(
                list_price_cents=publication.list_price_cents,
                currency=publication.currency,
            ).model_dump(),
        }
        provider_enum = CatalogPurchaseProvider.STRIPE if provider.strip().lower() == STRIPE_PROVIDER else CatalogPurchaseProvider.APP

        if normalized_status in {"completed", "succeeded", "paid"}:
            outcome = await purchase_service.record_successful_purchase(
                db,
                user_id=buyer.user_id,
                catalog_cookbook_id=catalog_cookbook_id,
                provider_checkout_ref=provider_checkout_ref,
                provider_completion_ref=provider_completion_ref,
                provider=provider_enum,
                purchase_metadata=purchase_metadata,
                access_reason="Purchased cookbook access is now included for this chef",
            )
            await db.commit()
            return MarketplaceCompletionBundle(
                checkout_status=normalized_status,
                purchase_state=outcome.decision.purchase_state.value,
                ownership_granted=outcome.ownership_record is not None,
                ownership_recorded=outcome.created_ownership_record,
                replayed_completion=outcome.reused_existing_completion,
                catalog_cookbook_id=catalog_cookbook_id,
                marketplace_cookbook_publication_id=publication.marketplace_cookbook_publication_id,
                purchase_outcome=outcome,
            )

        failed_state = CatalogPurchaseState.CANCELLED if normalized_status in {"cancelled", "canceled"} else CatalogPurchaseState.FAILED
        outcome = await purchase_service.record_non_completed_purchase(
            db,
            user_id=buyer.user_id,
            catalog_cookbook_id=catalog_cookbook_id,
            state=failed_state,
            provider_checkout_ref=provider_checkout_ref,
            provider_completion_ref=provider_completion_ref,
            provider=provider_enum,
            failure_code=f"checkout_{normalized_status}",
            failure_message=f"Cookbook checkout ended with {normalized_status}.",
            purchase_metadata=purchase_metadata,
        )
        await db.commit()
        return MarketplaceCompletionBundle(
            checkout_status=normalized_status,
            purchase_state=outcome.decision.purchase_state.value,
            ownership_granted=False,
            ownership_recorded=False,
            replayed_completion=outcome.reused_existing_completion,
            catalog_cookbook_id=catalog_cookbook_id,
            marketplace_cookbook_publication_id=publication.marketplace_cookbook_publication_id,
            purchase_outcome=outcome,
        )

    def build_marketplace_revenue_share(self, *, list_price_cents: int, currency: str) -> CookbookRevenueShare:
        seller_share_cents = (list_price_cents * _MARKETPLACE_SELLER_SHARE_BPS) // 10000
        platform_share_cents = list_price_cents - seller_share_cents
        return CookbookRevenueShare(
            seller_share_cents=seller_share_cents,
            platform_share_cents=platform_share_cents,
            seller_share_ratio="70%",
            platform_share_ratio="30%",
            list_price_cents=list_price_cents,
            currency=currency.lower(),
        )

    def catalog_cookbook_id_for_publication(self, publication_id: uuid.UUID) -> uuid.UUID:
        return uuid.uuid5(_MARKETPLACE_CATALOG_ID_NAMESPACE, str(publication_id))

    async def handle_webhook(self, db: AsyncSession, *, body: bytes, signature: str | None) -> SubscriptionSnapshot:
        envelope = self._verify_and_parse_webhook(body=body, signature=signature)
        event_object = envelope.payload.get("data", {}).get("object") or {}
        event_type = envelope.event_type

        if event_type == "checkout.session.completed":
            subscription_id = event_object.get("subscription")
            customer_id = event_object.get("customer")
            user_id = event_object.get("client_reference_id") or event_object.get("metadata", {}).get("user_id")
            if not subscription_id or not customer_id or not user_id:
                raise StripeWebhookPayloadError("checkout.session.completed payload missing linkage fields")
            snapshot = await self._sync_subscription(
                db,
                subscription_id=subscription_id,
                customer_id=customer_id,
                user_id=uuid.UUID(str(user_id)),
                event_id=envelope.event_id,
                event_type=event_type,
            )
        elif event_type.startswith("customer.subscription."):
            subscription_id = event_object.get("id")
            customer_id = event_object.get("customer")
            metadata = event_object.get("metadata") or {}
            user_id = await self._resolve_subscription_webhook_user_id(
                db,
                subscription_id=subscription_id,
                customer_id=customer_id,
                metadata_user_id=metadata.get("user_id"),
            )
            if not subscription_id or not customer_id or not user_id:
                raise StripeWebhookPayloadError("subscription webhook payload missing linkage fields")
            snapshot = await self._sync_subscription(
                db,
                subscription_id=subscription_id,
                customer_id=customer_id,
                user_id=user_id,
                event_id=envelope.event_id,
                event_type=event_type,
            )
        else:
            raise StripeWebhookPayloadError(f"Unhandled Stripe event type: {event_type}")

        await db.commit()
        return snapshot

    async def _build_outstanding_balance_summary(self, db: AsyncSession, *, session) -> SessionOutstandingBalanceSummary:
        status = await GenerationBillingService().get_outstanding_balance_status(db, session=session)
        return SessionOutstandingBalanceSummary(
            has_outstanding_balance=status.has_outstanding_balance,
            can_retry_charge=status.can_retry_charge,
            billing_state=status.billing_state.value if status.billing_state else None,
            reason_code=status.reason_code,
            reason=status.reason,
            retry_attempted_at=status.retry_attempted_at,
            recovery_action={
                "kind": "update_payment_method",
                "label": "Update payment method",
                "session_id": session.session_id,
            } if status.can_retry_charge else None,
        )

    async def _require_owned_session(self, db: AsyncSession, *, session_id: uuid.UUID, user: UserProfile):
        from app.models.session import Session

        session = await db.get(Session, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != user.user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        return session

    async def _get_or_create_seller_payout_account(self, db: AsyncSession, *, user: UserProfile) -> SellerPayoutAccountRecord:
        result = await _session_exec(
            db,
            select(SellerPayoutAccountRecord).where(SellerPayoutAccountRecord.user_id == user.user_id),
        )
        record = result.scalars().first()
        if record is not None:
            return record
        record = SellerPayoutAccountRecord(
            user_id=user.user_id,
            onboarding_status=SellerPayoutOnboardingStatus.NOT_STARTED,
            charges_enabled=False,
            payouts_enabled=False,
            details_submitted=False,
            requirements_due=["identity_verification", "payout_account"],
            status_reason="Complete payout onboarding to publish and sell cookbooks.",
        )
        db.add(record)
        await db.flush()
        return record

    def _build_seller_payout_readiness(self, record: SellerPayoutAccountRecord) -> SellerPayoutReadinessBundle:
        can_accept_sales = record.charges_enabled and record.payouts_enabled
        return SellerPayoutReadinessBundle(
            onboarding_status=record.onboarding_status.value,
            can_accept_sales=can_accept_sales,
            charges_enabled=record.charges_enabled,
            payouts_enabled=record.payouts_enabled,
            details_submitted=record.details_submitted,
            requirements_due=list(record.requirements_due or []),
            status_reason=record.status_reason,
            has_onboarding_action=not can_accept_sales,
        )

    async def _resolve_subscription_webhook_user_id(
        self,
        db: AsyncSession,
        *,
        subscription_id: str | None,
        customer_id: str | None,
        metadata_user_id: str | None,
    ) -> uuid.UUID | None:
        if metadata_user_id:
            try:
                return uuid.UUID(str(metadata_user_id))
            except (TypeError, ValueError) as exc:
                raise StripeWebhookPayloadError("subscription webhook metadata user_id is invalid") from exc

        if subscription_id:
            existing = await self._find_snapshot_by_provider_subscription_ref(
                db,
                provider_subscription_ref=subscription_id,
            )
            if existing is not None:
                return existing.user_id

        if customer_id:
            existing = await self._find_snapshot_by_provider_customer_ref(
                db,
                provider_customer_ref=customer_id,
            )
            if existing is not None:
                return existing.user_id

        return None

    async def _sync_subscription(
        self,
        db: AsyncSession,
        *,
        subscription_id: str,
        customer_id: str,
        user_id: uuid.UUID,
        event_id: str,
        event_type: str,
    ) -> SubscriptionSnapshot:
        existing = await self._find_snapshot_by_provider_subscription_ref(db, provider_subscription_ref=subscription_id)
        if existing and existing.sync_error_code == f"event:{event_id}":
            raise StripeReplayError(f"Stripe event {event_id} already processed")

        subscription = await self._gateway.retrieve_subscription(subscription_id)
        status = self._map_status(subscription.get("status"))
        current_period_end = _from_unix_ts(subscription.get("current_period_end"))
        user = await db.get(UserProfile, user_id)
        if user is None:
            raise StripeWebhookPayloadError(f"Unknown user for Stripe subscription sync: {user_id}")
        if user.stripe_customer_id != customer_id:
            user.stripe_customer_id = customer_id
            db.add(user)

        snapshot = await self._upsert_snapshot(
            db,
            user_id=user_id,
            provider_customer_ref=customer_id,
            provider_subscription_ref=subscription_id,
            status=status,
            sync_state=SubscriptionSyncState.SYNCED,
            plan_code=self._map_plan_code(subscription),
            current_period_ends_at=current_period_end,
            error_code=f"event:{event_id}",
            error_message=f"last_event={event_type}",
        )
        await self._upsert_premium_entitlement(
            db,
            user_id=user_id,
            active=status in {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.GRACE_PERIOD},
            current_period_ends_at=current_period_end,
        )
        logger.info(
            "stripe billing sync applied",
            extra={
                "user_id": str(user_id),
                "provider_subscription_ref": subscription_id,
                "snapshot_id": str(snapshot.subscription_snapshot_id),
                "sync_state": snapshot.sync_state.value,
                "status": snapshot.status.value,
                "event_id": event_id,
            },
        )
        return snapshot

    async def record_webhook_failure(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID | None,
        provider_customer_ref: str | None,
        provider_subscription_ref: str | None,
        error_code: str,
        error_message: str,
    ) -> SubscriptionSnapshot | None:
        if user_id is None and provider_customer_ref is None and provider_subscription_ref is None:
            return None

        snapshot = None
        if provider_subscription_ref:
            snapshot = await self._find_snapshot_by_provider_subscription_ref(
                db, provider_subscription_ref=provider_subscription_ref
            )
        if snapshot is None and user_id is not None:
            snapshot = await self._get_latest_snapshot(db, user_id=user_id)
        if snapshot is None and user_id is not None:
            snapshot = await self._upsert_snapshot(
                db,
                user_id=user_id,
                provider_customer_ref=provider_customer_ref,
                provider_subscription_ref=provider_subscription_ref,
                status=SubscriptionStatus.CANCELLED,
                sync_state=SubscriptionSyncState.FAILED,
                plan_code=_PREMIUM_PLAN_CODE,
                error_code=error_code,
                error_message=error_message,
            )
        elif snapshot is not None:
            snapshot.sync_state = SubscriptionSyncState.FAILED
            snapshot.sync_error_code = error_code
            snapshot.sync_error_message = error_message[:500]
            snapshot.last_synced_at = self._now_fn()
            snapshot.updated_at = self._now_fn()
            db.add(snapshot)
        if snapshot is not None:
            logger.warning(
                "stripe billing sync failed",
                extra={
                    "user_id": str(snapshot.user_id),
                    "snapshot_id": str(snapshot.subscription_snapshot_id),
                    "error_code": error_code,
                },
            )
        return snapshot

    async def _ensure_customer_ref(self, db: AsyncSession, *, user: UserProfile) -> str:
        existing = await self._get_latest_snapshot(db, user_id=user.user_id)
        if existing and existing.provider_customer_ref:
            return existing.provider_customer_ref
        if user.stripe_customer_id:
            return user.stripe_customer_id

        customer = await self._gateway.create_customer(
            email=user.email,
            name=user.name,
            metadata={"user_id": str(user.user_id)},
        )
        user.stripe_customer_id = customer["id"]
        db.add(user)
        snapshot = await self._upsert_snapshot(
            db,
            user_id=user.user_id,
            provider_customer_ref=customer["id"],
            provider_subscription_ref=None,
            status=SubscriptionStatus.CANCELLED,
            sync_state=SubscriptionSyncState.PENDING,
            plan_code=_PREMIUM_PLAN_CODE,
            error_code=None,
            error_message=None,
        )
        await db.flush()
        return snapshot.provider_customer_ref or customer["id"]

    async def _get_latest_snapshot(self, db: AsyncSession, *, user_id: uuid.UUID) -> SubscriptionSnapshot | None:
        result = await _session_exec(
            db,
            select(SubscriptionSnapshot)
            .where(SubscriptionSnapshot.user_id == user_id)
            .order_by(SubscriptionSnapshot.updated_at.desc(), SubscriptionSnapshot.created_at.desc()),
        )
        return result.scalars().first()

    async def _find_snapshot_by_provider_subscription_ref(
        self,
        db: AsyncSession,
        *,
        provider_subscription_ref: str,
    ) -> SubscriptionSnapshot | None:
        result = await _session_exec(
            db,
            select(SubscriptionSnapshot).where(
                SubscriptionSnapshot.provider_subscription_ref == provider_subscription_ref
            ),
        )
        return result.scalar_one_or_none()

    async def _find_snapshot_by_provider_customer_ref(
        self,
        db: AsyncSession,
        *,
        provider_customer_ref: str,
    ) -> SubscriptionSnapshot | None:
        result = await _session_exec(
            db,
            select(SubscriptionSnapshot)
            .where(SubscriptionSnapshot.provider_customer_ref == provider_customer_ref)
            .order_by(SubscriptionSnapshot.updated_at.desc(), SubscriptionSnapshot.created_at.desc()),
        )
        return result.scalars().first()

    async def _upsert_snapshot(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        provider_customer_ref: str | None,
        provider_subscription_ref: str | None,
        status: SubscriptionStatus,
        sync_state: SubscriptionSyncState,
        plan_code: str | None,
        error_code: str | None,
        error_message: str | None,
        current_period_ends_at: datetime | None = None,
    ) -> SubscriptionSnapshot:
        snapshot = None
        if provider_subscription_ref:
            snapshot = await self._find_snapshot_by_provider_subscription_ref(
                db, provider_subscription_ref=provider_subscription_ref
            )
        if snapshot is None:
            snapshot = await self._get_latest_snapshot(db, user_id=user_id)
        now = self._now_fn()
        if snapshot is None:
            snapshot = SubscriptionSnapshot(
                user_id=user_id,
                provider=STRIPE_PROVIDER,
            )
        snapshot.user_id = user_id
        snapshot.provider = STRIPE_PROVIDER
        snapshot.provider_customer_ref = provider_customer_ref or snapshot.provider_customer_ref
        snapshot.provider_subscription_ref = provider_subscription_ref or snapshot.provider_subscription_ref
        snapshot.plan_code = plan_code
        snapshot.status = status
        snapshot.sync_state = sync_state
        snapshot.current_period_ends_at = current_period_ends_at
        snapshot.last_synced_at = now
        snapshot.sync_error_code = error_code
        snapshot.sync_error_message = error_message[:500] if error_message else None
        snapshot.updated_at = now
        db.add(snapshot)
        await db.flush()
        return snapshot

    async def _upsert_premium_entitlement(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        active: bool,
        current_period_ends_at: datetime | None,
    ) -> UserEntitlementGrant:
        result = await _session_exec(
            db,
            select(UserEntitlementGrant).where(
                UserEntitlementGrant.user_id == user_id,
                UserEntitlementGrant.kind == EntitlementKind.CATALOG_PREMIUM,
                UserEntitlementGrant.source == STRIPE_PROVIDER,
            ),
        )
        grant = result.scalars().first()
        now = self._now_fn()
        if grant is None:
            grant = UserEntitlementGrant(
                user_id=user_id,
                kind=EntitlementKind.CATALOG_PREMIUM,
                source=STRIPE_PROVIDER,
                starts_at=now if active else None,
            )
        if active and grant.starts_at is None:
            grant.starts_at = now
        grant.is_active = active
        grant.ends_at = None if active else current_period_ends_at or now
        grant.updated_at = now
        db.add(grant)
        await db.flush()
        return grant

    def _verify_and_parse_webhook(self, *, body: bytes, signature: str | None) -> StripeWebhookEnvelope:
        if not signature:
            raise StripeSignatureError("Missing Stripe signature header")
        parts = {}
        for item in signature.split(","):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            parts[key] = value
        timestamp = parts.get("t")
        provided_signature = parts.get("v1")
        if not timestamp or not provided_signature:
            raise StripeSignatureError("Malformed Stripe signature header")
        try:
            timestamp_int = int(timestamp)
        except ValueError as exc:
            raise StripeSignatureError("Invalid Stripe signature timestamp") from exc
        if abs(int(time.time()) - timestamp_int) > self._settings.stripe_webhook_tolerance_seconds:
            raise StripeSignatureError("Stripe signature timestamp outside tolerance")

        signed_payload = f"{timestamp}.{body.decode('utf-8')}".encode("utf-8")
        expected = hmac.new(
            self._settings.stripe_webhook_secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, provided_signature):
            raise StripeSignatureError("Invalid Stripe signature")

        payload = json.loads(body.decode("utf-8"))
        event_id = payload.get("id")
        event_type = payload.get("type")
        if not event_id or not event_type:
            raise StripeWebhookPayloadError("Stripe event payload missing id or type")
        return StripeWebhookEnvelope(event_id=event_id, event_type=event_type, payload=payload)

    def _map_status(self, status: str | None) -> SubscriptionStatus:
        mapping = {
            "active": SubscriptionStatus.ACTIVE,
            "trialing": SubscriptionStatus.TRIALING,
            "past_due": SubscriptionStatus.PAST_DUE,
            "canceled": SubscriptionStatus.CANCELLED,
            "cancelled": SubscriptionStatus.CANCELLED,
            "unpaid": SubscriptionStatus.EXPIRED,
            "incomplete_expired": SubscriptionStatus.EXPIRED,
            "incomplete": SubscriptionStatus.PAST_DUE,
        }
        return mapping.get((status or "").lower(), SubscriptionStatus.CANCELLED)

    def _map_plan_code(self, subscription: dict[str, Any]) -> str:
        items = subscription.get("items", {}).get("data", [])
        for item in items:
            price = item.get("price") or {}
            if price.get("id") == self._settings.stripe_price_id:
                return _PREMIUM_PLAN_CODE
        return _PREMIUM_PLAN_CODE


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _from_unix_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)


def build_billing_service(settings) -> StripeBillingService:
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    return StripeBillingService(settings=settings)
