from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.deps import CurrentUser, DBSession
from app.core.settings import get_settings
from app.services.stripe_billing import (
    StripeReplayError,
    StripeSignatureError,
    StripeWebhookPayloadError,
    build_billing_service,
)

router = APIRouter(prefix="/billing", tags=["billing"])


def _app_safe_sync_payload(bundle) -> dict:
    return {
        "url": bundle.url,
        "subscription_status": bundle.subscription_status,
        "sync_state": bundle.sync_state,
        "subscription_snapshot_id": str(bundle.snapshot_id) if bundle.snapshot_id else None,
    }


@router.post("/checkout")
async def create_checkout_session(db: DBSession, current_user: CurrentUser):
    service = build_billing_service(get_settings())
    bundle = await service.create_checkout_session(db, user=current_user)
    return _app_safe_sync_payload(bundle)


@router.post("/portal")
async def create_portal_session(db: DBSession, current_user: CurrentUser):
    service = build_billing_service(get_settings())
    bundle = await service.create_portal_session(db, user=current_user)
    return _app_safe_sync_payload(bundle)


@router.post("/webhooks/stripe", status_code=status.HTTP_200_OK)
async def receive_stripe_webhook(request: Request, db: DBSession):
    settings = get_settings()
    service = build_billing_service(settings)
    body = await request.body()
    signature = request.headers.get("stripe-signature")

    payload = {}
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError:
        payload = {}

    event_object = payload.get("data", {}).get("object") or {}
    metadata = event_object.get("metadata") or {}
    user_id = None
    try:
        raw_user_id = event_object.get("client_reference_id") or metadata.get("user_id")
        user_id = uuid.UUID(str(raw_user_id)) if raw_user_id else None
    except (ValueError, TypeError):
        user_id = None

    try:
        snapshot = await service.handle_webhook(db, body=body, signature=signature)
        return {
            "received": True,
            "subscription_snapshot_id": str(snapshot.subscription_snapshot_id),
            "sync_state": snapshot.sync_state.value,
            "subscription_status": snapshot.status.value,
        }
    except StripeReplayError as exc:
        snapshot = await service.record_webhook_failure(
            db,
            user_id=user_id,
            provider_customer_ref=event_object.get("customer"),
            provider_subscription_ref=event_object.get("subscription") or event_object.get("id"),
            error_code="stripe_replay",
            error_message=str(exc),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "stripe_replay",
                "message": str(exc),
                "subscription_snapshot_id": str(snapshot.subscription_snapshot_id) if snapshot else None,
            },
        ) from exc
    except StripeSignatureError as exc:
        snapshot = await service.record_webhook_failure(
            db,
            user_id=user_id,
            provider_customer_ref=event_object.get("customer"),
            provider_subscription_ref=event_object.get("subscription") or event_object.get("id"),
            error_code="stripe_signature_invalid",
            error_message=str(exc),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "stripe_signature_invalid",
                "message": str(exc),
                "subscription_snapshot_id": str(snapshot.subscription_snapshot_id) if snapshot else None,
            },
        ) from exc
    except StripeWebhookPayloadError as exc:
        snapshot = await service.record_webhook_failure(
            db,
            user_id=user_id,
            provider_customer_ref=event_object.get("customer"),
            provider_subscription_ref=event_object.get("subscription") or event_object.get("id"),
            error_code="stripe_payload_invalid",
            error_message=str(exc),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "stripe_payload_invalid",
                "message": str(exc),
                "subscription_snapshot_id": str(snapshot.subscription_snapshot_id) if snapshot else None,
            },
        ) from exc
