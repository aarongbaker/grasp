from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, MultipleResultsFound
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import (
    CatalogCookbookOwnershipRecord,
    CatalogCookbookPurchaseRecord,
    CatalogPurchaseProvider,
    CatalogPurchaseState,
)


class CatalogPurchaseError(Exception):
    """Base app-owned catalog purchase error."""


class UnknownCatalogCookbookError(CatalogPurchaseError):
    """Raised when a purchase references an unknown catalog cookbook."""


@dataclass(frozen=True)
class CatalogPurchaseDecision:
    should_record_completion: bool
    purchase_state: CatalogPurchaseState
    reason: str
    failure_code: str | None = None


@dataclass(frozen=True)
class CatalogPurchaseOutcome:
    purchase_record: CatalogCookbookPurchaseRecord | None
    ownership_record: CatalogCookbookOwnershipRecord | None
    created_purchase_record: bool
    created_ownership_record: bool
    reused_existing_completion: bool
    decision: CatalogPurchaseDecision


class CatalogPurchaseService:
    """Exact-once persistence seam for durable catalog cookbook ownership."""

    def __init__(self, *, known_catalog_cookbook_ids: set[uuid.UUID], now_fn=None):
        self._known_catalog_cookbook_ids = set(known_catalog_cookbook_ids)
        self._now_fn = now_fn or _utcnow

    async def record_successful_purchase(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        catalog_cookbook_id: uuid.UUID,
        provider_checkout_ref: str | None,
        provider_completion_ref: str,
        provider: CatalogPurchaseProvider = CatalogPurchaseProvider.APP,
        purchase_metadata: dict | None = None,
        access_reason: str = "Purchased cookbook access is now included for this chef",
    ) -> CatalogPurchaseOutcome:
        self._require_known_catalog_cookbook(catalog_cookbook_id)
        now = self._now_fn()

        existing_purchase = await self.get_purchase_by_completion_ref(
            db,
            provider_completion_ref=provider_completion_ref,
        )
        if existing_purchase is not None:
            ownership = await self.get_ownership_for_purchase(
                db,
                purchase_record_id=existing_purchase.catalog_cookbook_purchase_record_id,
            )
            return CatalogPurchaseOutcome(
                purchase_record=existing_purchase,
                ownership_record=ownership,
                created_purchase_record=False,
                created_ownership_record=False,
                reused_existing_completion=True,
                decision=CatalogPurchaseDecision(
                    should_record_completion=True,
                    purchase_state=CatalogPurchaseState.COMPLETED,
                    reason="purchase completion already recorded",
                ),
            )

        purchase_record = CatalogCookbookPurchaseRecord(
            user_id=user_id,
            catalog_cookbook_id=catalog_cookbook_id,
            provider=provider,
            provider_checkout_ref=_truncate(provider_checkout_ref, 255),
            provider_completion_ref=_truncate(provider_completion_ref, 255),
            purchase_state=CatalogPurchaseState.COMPLETED,
            access_reason=access_reason,
            purchase_metadata=_copy_json(purchase_metadata),
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(purchase_record)
        try:
            await db.flush()
            created_purchase_record = True
        except IntegrityError:
            await db.rollback()
            existing_purchase = await self.get_purchase_by_completion_ref(
                db,
                provider_completion_ref=provider_completion_ref,
            )
            if existing_purchase is None:
                raise
            ownership = await self.get_ownership_for_purchase(
                db,
                purchase_record_id=existing_purchase.catalog_cookbook_purchase_record_id,
            )
            return CatalogPurchaseOutcome(
                purchase_record=existing_purchase,
                ownership_record=ownership,
                created_purchase_record=False,
                created_ownership_record=False,
                reused_existing_completion=True,
                decision=CatalogPurchaseDecision(
                    should_record_completion=True,
                    purchase_state=CatalogPurchaseState.COMPLETED,
                    reason="purchase completion already recorded",
                ),
            )

        ownership_record = await self.get_ownership_for_user_and_catalog(
            db,
            user_id=user_id,
            catalog_cookbook_id=catalog_cookbook_id,
        )
        if ownership_record is not None:
            return CatalogPurchaseOutcome(
                purchase_record=purchase_record,
                ownership_record=ownership_record,
                created_purchase_record=created_purchase_record,
                created_ownership_record=False,
                reused_existing_completion=False,
                decision=CatalogPurchaseDecision(
                    should_record_completion=True,
                    purchase_state=CatalogPurchaseState.COMPLETED,
                    reason="existing ownership reused",
                ),
            )

        ownership_record = CatalogCookbookOwnershipRecord(
            user_id=user_id,
            catalog_cookbook_id=catalog_cookbook_id,
            purchase_record_id=purchase_record.catalog_cookbook_purchase_record_id,
            ownership_source="purchase",
            access_reason=access_reason,
            ownership_metadata={
                "provider": provider.value,
                "provider_checkout_ref": _truncate(provider_checkout_ref, 255),
                "provider_completion_ref": _truncate(provider_completion_ref, 255),
            },
            acquired_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(ownership_record)
        try:
            await db.flush()
            created_ownership_record = True
        except IntegrityError:
            await db.rollback()
            existing_purchase = await self.get_purchase_by_completion_ref(
                db,
                provider_completion_ref=provider_completion_ref,
            )
            ownership_record = await self.get_ownership_for_user_and_catalog(
                db,
                user_id=user_id,
                catalog_cookbook_id=catalog_cookbook_id,
            )
            if existing_purchase is None or ownership_record is None:
                raise
            return CatalogPurchaseOutcome(
                purchase_record=existing_purchase,
                ownership_record=ownership_record,
                created_purchase_record=False,
                created_ownership_record=False,
                reused_existing_completion=True,
                decision=CatalogPurchaseDecision(
                    should_record_completion=True,
                    purchase_state=CatalogPurchaseState.COMPLETED,
                    reason="purchase completion already recorded",
                ),
            )

        return CatalogPurchaseOutcome(
            purchase_record=purchase_record,
            ownership_record=ownership_record,
            created_purchase_record=created_purchase_record,
            created_ownership_record=created_ownership_record,
            reused_existing_completion=False,
            decision=CatalogPurchaseDecision(
                should_record_completion=True,
                purchase_state=CatalogPurchaseState.COMPLETED,
                reason="purchase completion recorded",
            ),
        )

    async def record_non_completed_purchase(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        catalog_cookbook_id: uuid.UUID,
        state: CatalogPurchaseState,
        provider_checkout_ref: str | None,
        provider_completion_ref: str | None,
        provider: CatalogPurchaseProvider = CatalogPurchaseProvider.APP,
        failure_code: str | None = None,
        failure_message: str | None = None,
        purchase_metadata: dict | None = None,
    ) -> CatalogPurchaseOutcome:
        self._require_known_catalog_cookbook(catalog_cookbook_id)
        if state not in {CatalogPurchaseState.CANCELLED, CatalogPurchaseState.FAILED, CatalogPurchaseState.PENDING}:
            raise ValueError(f"Non-completed purchase state expected, got {state.value}")

        decision = CatalogPurchaseDecision(
            should_record_completion=False,
            purchase_state=state,
            reason="purchase completion skipped for non-completed state",
            failure_code=failure_code,
        )

        if provider_completion_ref:
            existing_purchase = await self.get_purchase_by_completion_ref(
                db,
                provider_completion_ref=provider_completion_ref,
            )
            if existing_purchase is not None:
                ownership = await self.get_ownership_for_purchase(
                    db,
                    purchase_record_id=existing_purchase.catalog_cookbook_purchase_record_id,
                )
                return CatalogPurchaseOutcome(
                    purchase_record=existing_purchase,
                    ownership_record=ownership,
                    created_purchase_record=False,
                    created_ownership_record=False,
                    reused_existing_completion=ownership is not None,
                    decision=decision,
                )

        now = self._now_fn()
        purchase_record = CatalogCookbookPurchaseRecord(
            user_id=user_id,
            catalog_cookbook_id=catalog_cookbook_id,
            provider=provider,
            provider_checkout_ref=_truncate(provider_checkout_ref, 255),
            provider_completion_ref=_truncate(provider_completion_ref, 255),
            purchase_state=state,
            access_reason="Catalog cookbook purchase did not complete",
            purchase_metadata=_copy_json(purchase_metadata),
            failure_code=_truncate(failure_code, 100),
            failure_message=_truncate(failure_message, 500),
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(purchase_record)
        try:
            await db.flush()
            created_purchase_record = True
        except IntegrityError:
            await db.rollback()
            existing_purchase = await self.get_purchase_by_completion_ref(
                db,
                provider_completion_ref=provider_completion_ref or "",
            ) if provider_completion_ref else None
            if existing_purchase is None:
                raise
            ownership = await self.get_ownership_for_purchase(
                db,
                purchase_record_id=existing_purchase.catalog_cookbook_purchase_record_id,
            )
            return CatalogPurchaseOutcome(
                purchase_record=existing_purchase,
                ownership_record=ownership,
                created_purchase_record=False,
                created_ownership_record=False,
                reused_existing_completion=ownership is not None,
                decision=decision,
            )

        return CatalogPurchaseOutcome(
            purchase_record=purchase_record,
            ownership_record=None,
            created_purchase_record=created_purchase_record,
            created_ownership_record=False,
            reused_existing_completion=False,
            decision=decision,
        )

    async def has_owned_catalog_cookbook(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        catalog_cookbook_id: uuid.UUID,
    ) -> bool:
        ownership = await self.get_ownership_for_user_and_catalog(
            db,
            user_id=user_id,
            catalog_cookbook_id=catalog_cookbook_id,
        )
        return ownership is not None

    async def get_ownership_for_user_and_catalog(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        catalog_cookbook_id: uuid.UUID,
    ) -> CatalogCookbookOwnershipRecord | None:
        result = await db.execute(
            select(CatalogCookbookOwnershipRecord).where(
                CatalogCookbookOwnershipRecord.user_id == user_id,
                CatalogCookbookOwnershipRecord.catalog_cookbook_id == catalog_cookbook_id,
            )
        )
        return _scalar_one_or_none(result)

    async def get_purchase_by_completion_ref(
        self,
        db: AsyncSession,
        *,
        provider_completion_ref: str,
    ) -> CatalogCookbookPurchaseRecord | None:
        if not provider_completion_ref:
            return None
        result = await db.execute(
            select(CatalogCookbookPurchaseRecord).where(
                CatalogCookbookPurchaseRecord.provider_completion_ref == provider_completion_ref
            )
        )
        return _scalar_one_or_none(result)

    async def get_ownership_for_purchase(
        self,
        db: AsyncSession,
        *,
        purchase_record_id: uuid.UUID,
    ) -> CatalogCookbookOwnershipRecord | None:
        result = await db.execute(
            select(CatalogCookbookOwnershipRecord).where(
                CatalogCookbookOwnershipRecord.purchase_record_id == purchase_record_id
            )
        )
        return _scalar_one_or_none(result)

    def _require_known_catalog_cookbook(self, catalog_cookbook_id: uuid.UUID) -> None:
        if catalog_cookbook_id not in self._known_catalog_cookbook_ids:
            raise UnknownCatalogCookbookError(f"Unknown catalog cookbook: {catalog_cookbook_id}")


def _scalar_one_or_none(result):
    if result is None:
        return None
    scalar_one_or_none = getattr(result, "scalar_one_or_none", None)
    if callable(scalar_one_or_none):
        try:
            return scalar_one_or_none()
        except MultipleResultsFound:
            return None
    first = getattr(result, "first", None)
    if callable(first):
        row = first()
        if row is not None:
            return row[0] if isinstance(row, tuple) else row
    scalars = getattr(result, "scalars", None)
    if callable(scalars):
        scalar_result = scalars()
        scalar_first = getattr(scalar_result, "first", None)
        if callable(scalar_first):
            return scalar_first()
        all_rows = getattr(scalar_result, "all", None)
        if callable(all_rows):
            rows = all_rows()
            return rows[0] if rows else None
    return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _truncate(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    return value[:max_length]


def _copy_json(value: dict | None) -> dict:
    return dict(value or {})
