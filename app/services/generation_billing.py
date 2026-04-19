from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession as SAAsyncSession
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.enums import SessionStatus
from app.models.session import Session
from app.models.user import (
    GenerationBillingProvider,
    GenerationBillingRecord,
    GenerationBillingState,
    GenerationFundingGrant,
    GenerationFundingGrantState,
    GenerationFundingGrantType,
    GenerationFundingLedgerEntry,
    GenerationFundingLedgerEntryKind,
    UserProfile,
)


def _session_exec(db: AsyncSession | SAAsyncSession, statement):
    exec_method = getattr(db, "exec", None)
    if callable(exec_method):
        return exec_method(statement)
    return db.execute(statement)


@dataclass(frozen=True)
class GenerationSettlementDecision:
    billing_state: GenerationBillingState
    reason: str
    source_type: str
    funding_grant_id: uuid.UUID | None = None
    provider_error_code: str | None = None
    provider_error_message: str | None = None
    provider: GenerationBillingProvider = GenerationBillingProvider.APP


@dataclass(frozen=True)
class GenerationBillingDecision:
    should_create_ledger: bool
    billing_state: GenerationBillingState
    reason: str


@dataclass(frozen=True)
class GenerationBillingOutcome:
    record: GenerationBillingRecord | None
    created: bool
    decision: GenerationBillingDecision


@dataclass(frozen=True)
class GenerationRunGateDecision:
    can_run: bool
    requires_payment_method: bool
    reason_code: str
    reason: str


@dataclass(frozen=True)
class GenerationOutstandingBalanceStatus:
    has_outstanding_balance: bool
    can_retry_charge: bool
    billing_state: GenerationBillingState | None
    reason_code: str | None
    reason: str | None
    retry_attempted_at: datetime | None


class GenerationBillingService:
    """Provider-agnostic exact-once seam for generation billing ledger state."""

    def __init__(self, *, now_fn=None):
        self._now_fn = now_fn or _utcnow

    async def record_finalized_session(
        self,
        db: AsyncSession,
        *,
        session: Session,
        final_state: dict[str, Any],
        provider: GenerationBillingProvider = GenerationBillingProvider.APP,
    ) -> GenerationBillingOutcome:
        decision = self._decide_billability(session=session)
        if not decision.should_create_ledger:
            return GenerationBillingOutcome(record=None, created=False, decision=decision)

        existing = await self.get_record_for_session(db, session_id=session.session_id)
        if existing is not None:
            return GenerationBillingOutcome(record=existing, created=False, decision=decision)

        user = await db.get(UserProfile, session.user_id)
        if user is None:
            raise ValueError(f"User {session.user_id} not found for generation billing")

        settlement = await self._determine_settlement(db, session=session, user=user)
        now = self._now_fn()
        token_usage_snapshot = self._build_token_usage_snapshot(session=session, final_state=final_state)
        billing_metadata = self._build_billing_metadata(session=session, final_state=final_state)
        billing_metadata.update(
            {
                "settlement_source": settlement.source_type,
                "funding_grant_id": str(settlement.funding_grant_id) if settlement.funding_grant_id else None,
                "monthly_free_generations_remaining": user.monthly_free_generations_remaining,
            }
        )

        record = GenerationBillingRecord(
            session_id=session.session_id,
            user_id=session.user_id,
            funding_grant_id=settlement.funding_grant_id,
            session_status=session.status,
            billing_state=settlement.billing_state,
            provider=provider if settlement.source_type == "card" else settlement.provider,
            billing_source_type=settlement.source_type,
            billing_reason=settlement.reason,
            provider_error_code=settlement.provider_error_code,
            provider_error_message=settlement.provider_error_message,
            total_input_tokens=token_usage_snapshot["total_input_tokens"],
            total_output_tokens=token_usage_snapshot["total_output_tokens"],
            token_usage_snapshot=token_usage_snapshot,
            billing_metadata=billing_metadata,
            charge_attempted_at=now
            if settlement.billing_state in {
                GenerationBillingState.CHARGE_PENDING,
                GenerationBillingState.CHARGE_FAILED,
                GenerationBillingState.CHARGED,
            }
            else None,
            charged_at=now if settlement.billing_state == GenerationBillingState.CHARGED else None,
            created_at=now,
            updated_at=now,
        )
        db.add(record)
        try:
            await db.flush()
            await self._apply_settlement_side_effects(
                db,
                session=session,
                user=user,
                record=record,
                settlement=settlement,
                now=now,
            )
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing = await self.get_record_for_session(db, session_id=session.session_id)
            if existing is None:
                raise
            return GenerationBillingOutcome(record=existing, created=False, decision=decision)

        return GenerationBillingOutcome(record=record, created=True, decision=decision)

    async def evaluate_run_gate(
        self,
        db: AsyncSession,
        *,
        session: Session,
        user: UserProfile,
    ) -> GenerationRunGateDecision:
        if session.status != SessionStatus.PENDING:
            return GenerationRunGateDecision(
                can_run=False,
                requires_payment_method=False,
                reason_code="session_not_pending",
                reason=f"Session is already {session.status.value}",
            )

        if await self._has_non_card_coverage(db, user=user):
            return GenerationRunGateDecision(
                can_run=True,
                requires_payment_method=False,
                reason_code="ready_to_run",
                reason="Session can run immediately.",
            )

        if not user.generation_payment_method_required:
            return GenerationRunGateDecision(
                can_run=True,
                requires_payment_method=False,
                reason_code="ready_to_run",
                reason="Session can run immediately.",
            )

        if user.has_saved_generation_payment_method:
            return GenerationRunGateDecision(
                can_run=True,
                requires_payment_method=False,
                reason_code="ready_to_run",
                reason="Session can run immediately.",
            )

        return GenerationRunGateDecision(
            can_run=False,
            requires_payment_method=True,
            reason_code="payment_method_required",
            reason="A saved payment method is required before this session can run.",
        )

    async def get_outstanding_balance_status(
        self,
        db: AsyncSession,
        *,
        session: Session,
    ) -> GenerationOutstandingBalanceStatus:
        record = await self.get_record_for_session(db, session_id=session.session_id)
        if record is None:
            return GenerationOutstandingBalanceStatus(
                has_outstanding_balance=False,
                can_retry_charge=False,
                billing_state=None,
                reason_code=None,
                reason=None,
                retry_attempted_at=None,
            )

        if record.billing_state == GenerationBillingState.CHARGE_FAILED:
            return GenerationOutstandingBalanceStatus(
                has_outstanding_balance=True,
                can_retry_charge=True,
                billing_state=record.billing_state,
                reason_code="outstanding_balance_recoverable",
                reason="A completed session still has an unpaid balance that can be recovered.",
                retry_attempted_at=record.charge_attempted_at,
            )

        return GenerationOutstandingBalanceStatus(
            has_outstanding_balance=False,
            can_retry_charge=False,
            billing_state=record.billing_state,
            reason_code=None,
            reason=None,
            retry_attempted_at=record.charge_attempted_at,
        )

    async def get_record_for_session(
        self,
        db: AsyncSession | SAAsyncSession,
        *,
        session_id: uuid.UUID,
    ) -> GenerationBillingRecord | None:
        result = await _session_exec(
            db,
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id),
        )
        return result.scalars().first()

    async def mark_charge_pending(
        self,
        db: AsyncSession,
        *,
        record: GenerationBillingRecord,
        provider: GenerationBillingProvider,
    ) -> GenerationBillingRecord:
        now = self._now_fn()
        record.provider = provider
        record.billing_state = GenerationBillingState.CHARGE_PENDING
        record.charge_attempted_at = now
        record.updated_at = now
        db.add(record)
        await db.flush()
        return record

    async def mark_charge_succeeded(
        self,
        db: AsyncSession,
        *,
        record: GenerationBillingRecord,
        provider_charge_ref: str,
    ) -> GenerationBillingRecord:
        now = self._now_fn()
        record.billing_state = GenerationBillingState.CHARGED
        record.provider_charge_ref = provider_charge_ref[:255]
        record.provider_error_code = None
        record.provider_error_message = None
        record.charged_at = now
        record.updated_at = now
        if record.charge_attempted_at is None:
            record.charge_attempted_at = now
        db.add(record)
        await db.flush()
        return record

    async def mark_charge_failed(
        self,
        db: AsyncSession,
        *,
        record: GenerationBillingRecord,
        error_code: str,
        error_message: str,
    ) -> GenerationBillingRecord:
        now = self._now_fn()
        record.billing_state = GenerationBillingState.CHARGE_FAILED
        record.provider_error_code = error_code[:100]
        record.provider_error_message = error_message[:500]
        record.updated_at = now
        if record.charge_attempted_at is None:
            record.charge_attempted_at = now
        db.add(record)
        await db.flush()
        return record

    def _decide_billability(self, *, session: Session) -> GenerationBillingDecision:
        if session.status in {SessionStatus.COMPLETE, SessionStatus.PARTIAL}:
            return GenerationBillingDecision(
                should_create_ledger=True,
                billing_state=GenerationBillingState.READY,
                reason="terminal session produced billable output",
            )
        if session.status == SessionStatus.CANCELLED:
            return GenerationBillingDecision(
                should_create_ledger=False,
                billing_state=GenerationBillingState.SKIPPED,
                reason="cancelled sessions are not billable",
            )
        return GenerationBillingDecision(
            should_create_ledger=False,
            billing_state=GenerationBillingState.SKIPPED,
            reason="only successful terminal sessions create billing ledger rows",
        )

    async def _determine_settlement(
        self,
        db: AsyncSession,
        *,
        session: Session,
        user: UserProfile,
    ) -> GenerationSettlementDecision:
        if user.monthly_free_generations_remaining > 0:
            return GenerationSettlementDecision(
                billing_state=GenerationBillingState.CHARGED,
                reason="covered by free usage allowance",
                source_type="free_allowance",
            )

        grant = await self._select_funding_grant(db, user_id=user.user_id)
        if grant is not None:
            return GenerationSettlementDecision(
                billing_state=GenerationBillingState.CHARGED,
                reason=f"covered by {grant.grant_type.value}",
                source_type=grant.grant_type.value,
                funding_grant_id=grant.generation_funding_grant_id,
            )

        if user.has_saved_generation_payment_method:
            return GenerationSettlementDecision(
                billing_state=GenerationBillingState.CHARGE_PENDING,
                reason="requires saved-card fallback charge",
                source_type="card",
                provider=GenerationBillingProvider.STRIPE,
            )

        return GenerationSettlementDecision(
            billing_state=GenerationBillingState.CHARGE_FAILED,
            reason="no free allowance, credits, prepaid balance, or saved payment method available",
            source_type="uncovered",
            provider_error_code="payment_method_required",
            provider_error_message="No saved payment method is available for saved-card fallback.",
        )

    async def _apply_settlement_side_effects(
        self,
        db: AsyncSession,
        *,
        session: Session,
        user: UserProfile,
        record: GenerationBillingRecord,
        settlement: GenerationSettlementDecision,
        now: datetime,
    ) -> None:
        if settlement.source_type == "free_allowance":
            user.monthly_free_generations_remaining = max(0, user.monthly_free_generations_remaining - 1)
            db.add(user)
            db.add(
                GenerationFundingLedgerEntry(
                    user_id=user.user_id,
                    session_id=session.session_id,
                    generation_billing_record_id=record.generation_billing_record_id,
                    funding_grant_id=None,
                    entry_kind=GenerationFundingLedgerEntryKind.DEBIT,
                    funding_source_type="free_allowance",
                    amount=-1,
                    balance_after=user.monthly_free_generations_remaining,
                    description="Consumed one free generation allowance.",
                    entry_metadata={"session_status": session.status.value},
                    created_at=now,
                )
            )
            return

        if settlement.funding_grant_id is not None:
            grant = await db.get(GenerationFundingGrant, settlement.funding_grant_id)
            if grant is None:
                raise ValueError(f"Funding grant {settlement.funding_grant_id} disappeared during settlement")
            grant.remaining_amount = max(0, grant.remaining_amount - 1)
            if grant.remaining_amount == 0:
                grant.grant_state = GenerationFundingGrantState.EXHAUSTED
            grant.updated_at = now
            db.add(grant)
            db.add(
                GenerationFundingLedgerEntry(
                    user_id=user.user_id,
                    session_id=session.session_id,
                    generation_billing_record_id=record.generation_billing_record_id,
                    funding_grant_id=grant.generation_funding_grant_id,
                    entry_kind=GenerationFundingLedgerEntryKind.DEBIT,
                    funding_source_type=settlement.source_type,
                    amount=-1,
                    balance_after=grant.remaining_amount,
                    description=f"Consumed one {settlement.source_type} unit.",
                    entry_metadata={"session_status": session.status.value},
                    created_at=now,
                )
            )
            return

        db.add(
            GenerationFundingLedgerEntry(
                user_id=user.user_id,
                session_id=session.session_id,
                generation_billing_record_id=record.generation_billing_record_id,
                funding_grant_id=None,
                entry_kind=GenerationFundingLedgerEntryKind.DEBIT,
                funding_source_type=settlement.source_type,
                amount=-1,
                balance_after=None,
                description=settlement.reason,
                entry_metadata={
                    "session_status": session.status.value,
                    "billing_state": settlement.billing_state.value,
                },
                created_at=now,
            )
        )

    async def _has_non_card_coverage(self, db: AsyncSession, *, user: UserProfile) -> bool:
        if user.monthly_free_generations_remaining > 0:
            return True
        grant = await self._select_funding_grant(db, user_id=user.user_id)
        return grant is not None

    async def _select_funding_grant(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
    ) -> GenerationFundingGrant | None:
        result = await _session_exec(
            db,
            select(GenerationFundingGrant)
            .where(GenerationFundingGrant.user_id == user_id)
            .where(GenerationFundingGrant.grant_state == GenerationFundingGrantState.ACTIVE)
            .where(GenerationFundingGrant.remaining_amount > 0)
            .order_by(
                GenerationFundingGrant.priority_bucket.asc(),
                GenerationFundingGrant.expires_at.asc().nulls_last(),
                GenerationFundingGrant.created_at.asc(),
            ),
        )
        return result.scalars().first()

    def _build_token_usage_snapshot(self, *, session: Session, final_state: dict[str, Any]) -> dict[str, Any]:
        persisted = session.token_usage or {}
        per_node = persisted.get("per_node")
        if not per_node:
            per_node = final_state.get("token_usage", []) or []
        total_input = int(persisted.get("total_input_tokens") or sum(item.get("input_tokens", 0) for item in per_node))
        total_output = int(persisted.get("total_output_tokens") or sum(item.get("output_tokens", 0) for item in per_node))
        return {
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "per_node": per_node,
        }

    def _build_billing_metadata(self, *, session: Session, final_state: dict[str, Any]) -> dict[str, Any]:
        validated_recipes = final_state.get("validated_recipes") or session.result_recipes or []
        schedule = final_state.get("schedule") or session.result_schedule or {}
        errors = final_state.get("errors") or []
        return {
            "completed_at": session.completed_at.isoformat() if session.completed_at else None,
            "recipe_count": len(validated_recipes),
            "schedule_summary": session.schedule_summary or schedule.get("summary"),
            "total_duration_minutes": session.total_duration_minutes or schedule.get("total_duration_minutes"),
            "error_count": len(errors),
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
