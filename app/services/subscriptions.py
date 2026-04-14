"""Provider-agnostic subscription persistence and lookup helpers.

This module owns the persisted account-side billing snapshot used by access
resolution. It intentionally stores only normalized, app-level subscription
state so downstream consumers never need raw provider payloads.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import desc
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import (
    SubscriptionSnapshot,
    SubscriptionStatus,
    SubscriptionSyncState,
    UserEntitlementGrant,
)


@dataclass(frozen=True)
class SubscriptionDiagnostics:
    """Inspectable persistence surface for derived access decisions."""

    subscription_snapshot_id: uuid.UUID | None
    subscription_status: SubscriptionStatus | None
    sync_state: SubscriptionSyncState | None
    provider: str | None


async def get_active_subscription_snapshot(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> SubscriptionSnapshot | None:
    """Return the newest non-terminal subscription snapshot for a user."""

    stmt = (
        select(SubscriptionSnapshot)
        .where(SubscriptionSnapshot.user_id == user_id)
        .order_by(desc(SubscriptionSnapshot.updated_at), desc(SubscriptionSnapshot.created_at))
    )
    result = await db.exec(stmt)
    rows = result.all()
    for row in rows:
        if row.status in {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.GRACE_PERIOD}:
            return row
    return rows[0] if rows else None


async def list_user_entitlement_grants(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> list[UserEntitlementGrant]:
    """Return explicit, app-owned entitlement grants for a user."""

    stmt = (
        select(UserEntitlementGrant)
        .where(UserEntitlementGrant.user_id == user_id)
        .order_by(desc(UserEntitlementGrant.created_at))
    )
    result = await db.exec(stmt)
    return list(result.all())


def build_subscription_diagnostics(
    snapshot: SubscriptionSnapshot | None,
) -> SubscriptionDiagnostics:
    """Return provider-agnostic diagnostics for resolver output and logs."""

    if snapshot is None:
        return SubscriptionDiagnostics(
            subscription_snapshot_id=None,
            subscription_status=None,
            sync_state=None,
            provider=None,
        )
    return SubscriptionDiagnostics(
        subscription_snapshot_id=snapshot.subscription_snapshot_id,
        subscription_status=snapshot.status,
        sync_state=snapshot.sync_state,
        provider=snapshot.provider,
    )
