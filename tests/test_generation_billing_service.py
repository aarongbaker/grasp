from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.enums import SessionStatus
from app.models.session import Session
from app.models.user import (
    GenerationBillingProvider,
    GenerationBillingRecord,
    GenerationBillingState,
    GenerationFundingGrant,
    GenerationFundingGrantSource,
    GenerationFundingGrantState,
    GenerationFundingGrantType,
    GenerationFundingLedgerEntry,
    GenerationFundingLedgerEntryKind,
    UserProfile,
)
from app.services.generation_billing import GenerationBillingService
from tests.conftest import _ensure_test_postgres_available, register_test_sqlmodel_metadata, reset_test_database


@pytest.fixture
async def generation_billing_db():
    _ensure_test_postgres_available()
    from app.core.settings import get_settings

    settings = get_settings()
    register_test_sqlmodel_metadata()
    engine = create_async_engine(settings.test_database_url, echo=False, future=True, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, checkfirst=True)
        await conn.exec_driver_sql(
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS monthly_free_generations_remaining INTEGER NOT NULL DEFAULT 0"
        )
        await conn.exec_driver_sql(
            "ALTER TABLE generation_billing_records ADD COLUMN IF NOT EXISTS funding_grant_id UUID"
        )
        await conn.exec_driver_sql(
            "ALTER TABLE generation_billing_records ADD COLUMN IF NOT EXISTS billing_source_type VARCHAR(50)"
        )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await reset_test_database(session)
        await session.exec(text(
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS monthly_free_generations_remaining INTEGER NOT NULL DEFAULT 0"
        ))
        await session.exec(text(
            "ALTER TABLE generation_billing_records ADD COLUMN IF NOT EXISTS funding_grant_id UUID"
        ))
        await session.exec(text(
            "ALTER TABLE generation_billing_records ADD COLUMN IF NOT EXISTS billing_source_type VARCHAR(50)"
        ))
        await session.commit()
        yield session
        await session.rollback()

    await engine.dispose()


def _service(now: datetime | None = None) -> GenerationBillingService:
    now_value = now or datetime(2026, 4, 14, 21, 0, 0)
    return GenerationBillingService(now_fn=lambda: now_value)


async def _create_session(
    db: AsyncSession,
    *,
    status: SessionStatus,
    token_usage: dict | None = None,
    generation_payment_method_required: bool = False,
    has_saved_generation_payment_method: bool = False,
    monthly_free_generations_remaining: int = 0,
) -> tuple[UserProfile, Session]:
    user_id = uuid.uuid4()
    email = f"chef-{user_id}@test.com"
    user = UserProfile(
        user_id=user_id,
        name="Ledger Chef",
        email=email,
        rag_owner_key=UserProfile.build_rag_owner_key(email),
        generation_payment_method_required=generation_payment_method_required,
        has_saved_generation_payment_method=has_saved_generation_payment_method,
        monthly_free_generations_remaining=monthly_free_generations_remaining,
    )
    session = Session(
        session_id=uuid.uuid4(),
        user_id=user_id,
        status=status,
        concept_json={"free_text": "bill this generation"},
        schedule_summary="Recovered dinner" if status in {SessionStatus.COMPLETE, SessionStatus.PARTIAL} else None,
        total_duration_minutes=42 if status in {SessionStatus.COMPLETE, SessionStatus.PARTIAL} else None,
        token_usage=token_usage,
        completed_at=datetime(2026, 4, 14, 20, 55, 0),
        result_schedule={"summary": "Recovered dinner", "total_duration_minutes": 42}
        if status in {SessionStatus.COMPLETE, SessionStatus.PARTIAL}
        else None,
        result_recipes=[{"source": {"name": "Short Ribs"}}] if status in {SessionStatus.COMPLETE, SessionStatus.PARTIAL} else None,
    )
    db.add(user)
    await db.flush()
    db.add(session)
    await db.flush()
    await db.commit()
    persisted_user = await db.get(UserProfile, user_id)
    persisted_session = await db.get(Session, session.session_id)
    if persisted_user is None or persisted_session is None:
        raise AssertionError("expected persisted billing test fixtures")
    return persisted_user, persisted_session


async def _grant_funding(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    grant_type: GenerationFundingGrantType,
    amount: int,
    remaining: int | None = None,
    source: GenerationFundingGrantSource,
    cycle_key: str | None = None,
    expires_at: datetime | None = None,
) -> GenerationFundingGrant:
    user = await db.get(UserProfile, user_id)
    if user is None:
        raise AssertionError(f"expected persisted user fixture for funding grant {user_id}")

    grant = GenerationFundingGrant(
        user_id=user_id,
        grant_type=grant_type,
        source=source,
        grant_state=GenerationFundingGrantState.ACTIVE,
        amount=amount,
        remaining_amount=amount if remaining is None else remaining,
        currency="generation",
        priority_bucket=0 if grant_type == GenerationFundingGrantType.SUBSCRIPTION_CREDIT else 1,
        cycle_key=cycle_key,
        expires_at=expires_at,
    )
    db.add(grant)
    await db.flush()
    return grant


@pytest.mark.asyncio
async def test_record_finalized_session_creates_one_exact_once_ledger_row(generation_billing_db):
    service = _service()
    _user, session = await _create_session(
        generation_billing_db,
        status=SessionStatus.COMPLETE,
        token_usage={
            "total_input_tokens": 21,
            "total_output_tokens": 34,
            "per_node": [{"node_name": "recipe_generator", "input_tokens": 21, "output_tokens": 34}],
        },
    )

    first = await service.record_finalized_session(
        generation_billing_db,
        session=session,
        final_state={"validated_recipes": [{"source": {"name": "Short Ribs"}}], "errors": []},
    )
    await generation_billing_db.commit()

    second = await service.record_finalized_session(
        generation_billing_db,
        session=session,
        final_state={"validated_recipes": [{"source": {"name": "Short Ribs"}}], "errors": []},
    )
    await generation_billing_db.commit()

    records = (await generation_billing_db.exec(select(GenerationBillingRecord))).all()
    entries = (await generation_billing_db.exec(select(GenerationFundingLedgerEntry))).all()
    assert len(records) == 1
    assert len(entries) == 1
    assert first.created is True
    assert second.created is False
    assert first.record is not None
    assert second.record is not None
    assert first.record.generation_billing_record_id == second.record.generation_billing_record_id
    assert first.record.billing_state == GenerationBillingState.CHARGE_FAILED
    assert first.record.billing_source_type == "uncovered"
    assert first.record.total_input_tokens == 21
    assert first.record.total_output_tokens == 34
    assert first.record.billing_metadata["recipe_count"] == 1
    assert first.record.billing_metadata["schedule_summary"] == "Recovered dinner"
    assert entries[0].entry_kind == GenerationFundingLedgerEntryKind.DEBIT
    assert entries[0].funding_source_type == "uncovered"


@pytest.mark.asyncio
async def test_record_finalized_session_uses_free_allowance_before_other_sources(generation_billing_db):
    service = _service()
    user, session = await _create_session(
        generation_billing_db,
        status=SessionStatus.COMPLETE,
        monthly_free_generations_remaining=2,
        has_saved_generation_payment_method=True,
    )

    outcome = await service.record_finalized_session(generation_billing_db, session=session, final_state={})
    await generation_billing_db.commit()

    refreshed_user = await generation_billing_db.get(UserProfile, user.user_id)
    entries = (await generation_billing_db.exec(select(GenerationFundingLedgerEntry))).all()
    assert outcome.record is not None
    assert outcome.record.billing_state == GenerationBillingState.CHARGED
    assert outcome.record.billing_source_type == "free_allowance"
    assert outcome.record.billing_reason == "covered by free usage allowance"
    assert refreshed_user is not None
    assert refreshed_user.monthly_free_generations_remaining == 1
    assert len(entries) == 1
    assert entries[0].funding_source_type == "free_allowance"
    assert entries[0].entry_kind == GenerationFundingLedgerEntryKind.DEBIT


@pytest.mark.asyncio
async def test_record_finalized_session_uses_subscription_credit_before_prepaid_balance(generation_billing_db):
    service = _service()
    user, session = await _create_session(
        generation_billing_db,
        status=SessionStatus.COMPLETE,
        has_saved_generation_payment_method=True,
    )
    subscription_credit = await _grant_funding(
        generation_billing_db,
        user_id=user.user_id,
        grant_type=GenerationFundingGrantType.SUBSCRIPTION_CREDIT,
        amount=3,
        source=GenerationFundingGrantSource.SUBSCRIPTION,
        cycle_key="2026-04",
    )
    prepaid_balance = await _grant_funding(
        generation_billing_db,
        user_id=user.user_id,
        grant_type=GenerationFundingGrantType.PREPAID_BALANCE,
        amount=5,
        source=GenerationFundingGrantSource.PACK,
    )

    outcome = await service.record_finalized_session(generation_billing_db, session=session, final_state={})
    await generation_billing_db.commit()

    refreshed_subscription_credit = await generation_billing_db.get(
        GenerationFundingGrant,
        subscription_credit.generation_funding_grant_id,
    )
    refreshed_prepaid_balance = await generation_billing_db.get(
        GenerationFundingGrant,
        prepaid_balance.generation_funding_grant_id,
    )
    entries = (await generation_billing_db.exec(select(GenerationFundingLedgerEntry))).all()
    assert outcome.record is not None
    assert outcome.record.billing_state == GenerationBillingState.CHARGED
    assert outcome.record.billing_source_type == "subscription_credit"
    assert outcome.record.funding_grant_id == subscription_credit.generation_funding_grant_id
    assert refreshed_subscription_credit is not None
    assert refreshed_subscription_credit.remaining_amount == 2
    assert refreshed_prepaid_balance is not None
    assert refreshed_prepaid_balance.remaining_amount == 5
    assert len(entries) == 1
    assert entries[0].funding_source_type == "subscription_credit"
    assert entries[0].funding_grant_id == subscription_credit.generation_funding_grant_id


@pytest.mark.asyncio
async def test_record_finalized_session_uses_prepaid_balance_before_saved_card(generation_billing_db):
    service = _service()
    user, session = await _create_session(
        generation_billing_db,
        status=SessionStatus.COMPLETE,
        has_saved_generation_payment_method=True,
    )
    prepaid_balance = await _grant_funding(
        generation_billing_db,
        user_id=user.user_id,
        grant_type=GenerationFundingGrantType.PREPAID_BALANCE,
        amount=2,
        source=GenerationFundingGrantSource.PACK,
    )

    outcome = await service.record_finalized_session(generation_billing_db, session=session, final_state={})
    await generation_billing_db.commit()

    refreshed_prepaid_balance = await generation_billing_db.get(
        GenerationFundingGrant,
        prepaid_balance.generation_funding_grant_id,
    )
    assert outcome.record is not None
    assert outcome.record.billing_state == GenerationBillingState.CHARGED
    assert outcome.record.billing_source_type == "prepaid_balance"
    assert outcome.record.funding_grant_id == prepaid_balance.generation_funding_grant_id
    assert refreshed_prepaid_balance is not None
    assert refreshed_prepaid_balance.remaining_amount == 1


@pytest.mark.asyncio
async def test_record_finalized_session_falls_back_to_saved_card_when_no_credits_exist(generation_billing_db):
    service = _service()
    _user, session = await _create_session(
        generation_billing_db,
        status=SessionStatus.COMPLETE,
        has_saved_generation_payment_method=True,
    )

    outcome = await service.record_finalized_session(generation_billing_db, session=session, final_state={})
    await generation_billing_db.commit()

    assert outcome.record is not None
    assert outcome.record.billing_state == GenerationBillingState.CHARGE_PENDING
    assert outcome.record.billing_source_type == "card"
    assert outcome.record.billing_reason == "requires saved-card fallback charge"


@pytest.mark.asyncio
async def test_record_finalized_session_marks_charge_failure_when_no_coverage_or_card_exists(generation_billing_db):
    service = _service(now=datetime(2026, 4, 14, 21, 45, 0))
    _user, session = await _create_session(generation_billing_db, status=SessionStatus.COMPLETE)

    outcome = await service.record_finalized_session(generation_billing_db, session=session, final_state={})
    await generation_billing_db.commit()

    outstanding = await service.get_outstanding_balance_status(generation_billing_db, session=session)

    assert outcome.record is not None
    assert outcome.record.billing_state == GenerationBillingState.CHARGE_FAILED
    assert outcome.record.billing_source_type == "uncovered"
    assert outcome.record.provider_error_code == "payment_method_required"
    assert outstanding.has_outstanding_balance is True
    assert outstanding.can_retry_charge is True
    assert outstanding.billing_state == GenerationBillingState.CHARGE_FAILED
    assert outstanding.reason_code == "outstanding_balance_recoverable"
    assert outstanding.retry_attempted_at == datetime(2026, 4, 14, 21, 45, 0)


@pytest.mark.asyncio
async def test_record_finalized_session_replay_is_idempotent_for_credit_settlement(generation_billing_db):
    service = _service()
    user, session = await _create_session(generation_billing_db, status=SessionStatus.COMPLETE)
    subscription_credit = await _grant_funding(
        generation_billing_db,
        user_id=user.user_id,
        grant_type=GenerationFundingGrantType.SUBSCRIPTION_CREDIT,
        amount=1,
        source=GenerationFundingGrantSource.SUBSCRIPTION,
        cycle_key="2026-04",
    )

    first = await service.record_finalized_session(generation_billing_db, session=session, final_state={})
    await generation_billing_db.commit()
    second = await service.record_finalized_session(generation_billing_db, session=session, final_state={})
    await generation_billing_db.commit()

    refreshed_subscription_credit = await generation_billing_db.get(
        GenerationFundingGrant,
        subscription_credit.generation_funding_grant_id,
    )
    entries = (await generation_billing_db.exec(select(GenerationFundingLedgerEntry))).all()
    assert first.record is not None
    assert second.record is not None
    assert first.record.generation_billing_record_id == second.record.generation_billing_record_id
    assert refreshed_subscription_credit is not None
    assert refreshed_subscription_credit.remaining_amount == 0
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_record_finalized_session_skips_cancelled_and_failed_sessions(generation_billing_db):
    service = _service()
    _cancelled_user, cancelled = await _create_session(generation_billing_db, status=SessionStatus.CANCELLED, token_usage=None)
    _failed_user, failed = await _create_session(generation_billing_db, status=SessionStatus.FAILED, token_usage=None)

    cancelled_outcome = await service.record_finalized_session(generation_billing_db, session=cancelled, final_state={})
    failed_outcome = await service.record_finalized_session(generation_billing_db, session=failed, final_state={"errors": [{"message": "boom"}]})
    await generation_billing_db.commit()

    assert cancelled_outcome.record is None
    assert cancelled_outcome.decision.billing_state == GenerationBillingState.SKIPPED
    assert failed_outcome.record is None
    assert failed_outcome.decision.billing_state == GenerationBillingState.SKIPPED

    records = (await generation_billing_db.exec(select(GenerationBillingRecord))).all()
    assert records == []


@pytest.mark.asyncio
async def test_evaluate_run_gate_allows_coverage_without_saved_payment_method(generation_billing_db):
    service = _service()
    user, session = await _create_session(
        generation_billing_db,
        status=SessionStatus.PENDING,
        generation_payment_method_required=True,
        has_saved_generation_payment_method=False,
        monthly_free_generations_remaining=1,
    )

    decision = await service.evaluate_run_gate(generation_billing_db, session=session, user=user)

    assert decision.can_run is True
    assert decision.requires_payment_method is False
    assert decision.reason_code == "ready_to_run"


@pytest.mark.asyncio
async def test_evaluate_run_gate_blocks_when_saved_payment_method_is_required_and_no_coverage_exists(generation_billing_db):
    service = _service()
    user, session = await _create_session(
        generation_billing_db,
        status=SessionStatus.PENDING,
        generation_payment_method_required=True,
        has_saved_generation_payment_method=False,
    )

    decision = await service.evaluate_run_gate(generation_billing_db, session=session, user=user)

    assert decision.can_run is False
    assert decision.requires_payment_method is True
    assert decision.reason_code == "payment_method_required"


@pytest.mark.asyncio
async def test_charge_state_transitions_are_recorded_without_touching_session_status(generation_billing_db):
    service = _service(now=datetime(2026, 4, 14, 21, 30, 0))
    _user, session = await _create_session(
        generation_billing_db,
        status=SessionStatus.COMPLETE,
        token_usage={
            "total_input_tokens": 5,
            "total_output_tokens": 8,
            "per_node": [{"node_name": "renderer", "input_tokens": 5, "output_tokens": 8}],
        },
        has_saved_generation_payment_method=True,
    )

    outcome = await service.record_finalized_session(generation_billing_db, session=session, final_state={})
    assert outcome.record is not None
    record = outcome.record

    await service.mark_charge_pending(
        generation_billing_db,
        record=record,
        provider=GenerationBillingProvider.STRIPE,
    )
    await service.mark_charge_failed(
        generation_billing_db,
        record=record,
        error_code="provider_timeout",
        error_message="provider timed out after finalize",
    )
    await generation_billing_db.commit()

    refreshed_session = await generation_billing_db.get(Session, session.session_id)
    refreshed_record = await generation_billing_db.get(GenerationBillingRecord, record.generation_billing_record_id)
    assert refreshed_session is not None
    assert refreshed_session.status == SessionStatus.COMPLETE
    assert refreshed_record is not None
    assert refreshed_record.billing_state == GenerationBillingState.CHARGE_FAILED
    assert refreshed_record.provider == GenerationBillingProvider.STRIPE
    assert refreshed_record.provider_error_code == "provider_timeout"
    assert refreshed_record.charge_attempted_at is not None
