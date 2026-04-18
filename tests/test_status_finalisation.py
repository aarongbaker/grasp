import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.api.routes.sessions import cancel_pipeline
from app.core.status import finalise_session
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
    UserProfile,
)
from app.services.generation_billing import GenerationBillingService


def _current_user(user_id: uuid.UUID) -> UserProfile:
    email = f"chef-{user_id}@test.com"
    return UserProfile(
        user_id=user_id,
        name="Test Chef",
        email=email,
        rag_owner_key=UserProfile.build_rag_owner_key(email),
    )


def _schedule_payload(summary: str = "Ready") -> dict:
    return {
        "summary": summary,
        "timeline": [],
        "total_duration_minutes": 10,
        "error_summary": None,
    }


def _token_usage(*, input_tokens: int, output_tokens: int, node_name: str = "renderer") -> list[dict]:
    return [{"input_tokens": input_tokens, "output_tokens": output_tokens, "node_name": node_name}]


async def _grant_subscription_credit(test_db_session, *, user_id: uuid.UUID, amount: int = 1) -> GenerationFundingGrant:
    grant = GenerationFundingGrant(
        user_id=user_id,
        grant_type=GenerationFundingGrantType.SUBSCRIPTION_CREDIT,
        source=GenerationFundingGrantSource.SUBSCRIPTION,
        grant_state=GenerationFundingGrantState.ACTIVE,
        amount=amount,
        remaining_amount=amount,
        currency="generation",
        priority_bucket=0,
        cycle_key="2026-04",
    )
    test_db_session.add(grant)
    await test_db_session.commit()
    return grant


@pytest.mark.asyncio
async def test_finalise_session_preserves_cancelled_status(test_db_session, test_user_id):
    session_id = uuid.uuid4()
    completed_at = datetime(2026, 4, 8, 12, 0, 0)
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.CANCELLED,
        concept_json={"free_text": "cancelled run"},
        completed_at=completed_at,
    )
    test_db_session.add(session)
    await test_db_session.commit()

    await finalise_session(
        session_id,
        {
            "schedule": _schedule_payload("Should not win"),
            "validated_recipes": [],
            "errors": [],
        },
        test_db_session,
    )

    refreshed = await test_db_session.get(Session, session_id)
    ledger_rows = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalars().all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.CANCELLED
    assert refreshed.schedule_summary is None
    assert refreshed.result_schedule is None
    assert refreshed.completed_at == completed_at
    assert ledger_rows == []


@pytest.mark.asyncio
async def test_finalise_session_preserves_complete_status(test_db_session, test_user_id):
    session_id = uuid.uuid4()
    completed_at = datetime(2026, 4, 10, 9, 0, 0)
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.COMPLETE,
        concept_json={"free_text": "already complete"},
        schedule_summary="Original summary",
        completed_at=completed_at,
    )
    test_db_session.add(session)
    await test_db_session.commit()

    await finalise_session(
        session_id,
        {
            "schedule": _schedule_payload("Should not overwrite"),
            "validated_recipes": [],
            "errors": [],
        },
        test_db_session,
    )

    refreshed = await test_db_session.get(Session, session_id)
    ledger_rows = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalars().all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert refreshed.schedule_summary == "Original summary"
    assert refreshed.completed_at == completed_at
    assert ledger_rows == []


@pytest.mark.asyncio
async def test_finalise_session_preserves_partial_status(test_db_session, test_user_id):
    session_id = uuid.uuid4()
    completed_at = datetime(2026, 4, 10, 10, 0, 0)
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.PARTIAL,
        concept_json={"free_text": "already partial"},
        schedule_summary="Partial original",
        error_summary="enricher: dropped dessert",
        completed_at=completed_at,
    )
    test_db_session.add(session)
    await test_db_session.commit()

    await finalise_session(
        session_id,
        {
            "schedule": _schedule_payload("Should not overwrite"),
            "validated_recipes": [],
            "errors": [],
        },
        test_db_session,
    )

    refreshed = await test_db_session.get(Session, session_id)
    ledger_rows = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalars().all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.PARTIAL
    assert refreshed.schedule_summary == "Partial original"
    assert refreshed.error_summary == "enricher: dropped dessert"
    assert refreshed.completed_at == completed_at
    assert ledger_rows == []


@pytest.mark.asyncio
async def test_finalise_session_preserves_failed_status(test_db_session, test_user_id):
    session_id = uuid.uuid4()
    completed_at = datetime(2026, 4, 10, 11, 0, 0)
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.FAILED,
        concept_json={"free_text": "already failed"},
        error_summary="renderer: boom",
        completed_at=completed_at,
    )
    test_db_session.add(session)
    await test_db_session.commit()

    await finalise_session(
        session_id,
        {
            "schedule": _schedule_payload("Should not overwrite"),
            "validated_recipes": [],
            "errors": [],
        },
        test_db_session,
    )

    refreshed = await test_db_session.get(Session, session_id)
    ledger_rows = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalars().all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.FAILED
    assert refreshed.error_summary == "renderer: boom"
    assert refreshed.completed_at == completed_at
    assert ledger_rows == []


@pytest.mark.asyncio
async def test_finalise_session_persists_terminal_payload_for_uncancelled_session(test_db_session, test_user_id):
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json={"free_text": "happy path"},
    )
    test_db_session.add(session)
    await test_db_session.commit()

    final_state = {
        "schedule": _schedule_payload("Dinner ready"),
        "validated_recipes": [],
        "errors": [],
        "token_usage": [{"input_tokens": 12, "output_tokens": 8, "node_name": "renderer"}],
    }

    await finalise_session(session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, session_id)
    ledger_rows = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalars().all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert refreshed.schedule_summary == "Dinner ready"
    assert refreshed.total_duration_minutes == 10
    assert refreshed.result_schedule["summary"] == "Dinner ready"
    assert refreshed.result_recipes == []
    assert refreshed.token_usage["total_input_tokens"] == 12
    assert refreshed.token_usage["total_output_tokens"] == 8
    assert refreshed.completed_at is not None
    assert len(ledger_rows) == 1
    assert ledger_rows[0].billing_state == GenerationBillingState.CHARGE_FAILED
    assert ledger_rows[0].session_status == SessionStatus.COMPLETE
    assert ledger_rows[0].billing_source_type == "uncovered"
    assert ledger_rows[0].total_input_tokens == 12
    assert ledger_rows[0].total_output_tokens == 8


@pytest.mark.asyncio
async def test_finalise_session_reuses_existing_ledger_record_on_duplicate_call(test_db_session, test_user_id):
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json={"free_text": "dedupe me"},
    )
    test_db_session.add(session)
    await test_db_session.commit()

    final_state = {
        "schedule": _schedule_payload("Dinner ready"),
        "validated_recipes": [],
        "errors": [],
        "token_usage": _token_usage(input_tokens=3, output_tokens=2),
    }

    await finalise_session(session_id, final_state, test_db_session)
    await finalise_session(session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, session_id)
    ledger_rows = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalars().all()
    entries = (
        await test_db_session.execute(
            select(GenerationFundingLedgerEntry).where(GenerationFundingLedgerEntry.session_id == session_id)
        )
    ).scalars().all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert len(ledger_rows) == 1
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_finalise_session_duplicate_then_charge_failure_preserves_single_ledger_row_and_terminal_session(
    test_db_session,
    test_user_id,
):
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json={"free_text": "charge me once"},
    )
    test_db_session.add(session)
    await test_db_session.commit()

    final_state = {
        "schedule": _schedule_payload("Billable dinner ready"),
        "validated_recipes": [],
        "errors": [],
        "token_usage": _token_usage(input_tokens=13, output_tokens=8),
    }

    await finalise_session(session_id, final_state, test_db_session)
    await finalise_session(session_id, final_state, test_db_session)

    ledger_record = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalar_one()

    billing_service = GenerationBillingService(now_fn=lambda: datetime(2026, 4, 14, 22, 0, 0))
    await billing_service.mark_charge_pending(
        test_db_session,
        record=ledger_record,
        provider=GenerationBillingProvider.STRIPE,
    )
    await billing_service.mark_charge_failed(
        test_db_session,
        record=ledger_record,
        error_code="provider_timeout",
        error_message="provider timed out after finalize",
    )
    await test_db_session.commit()

    refreshed = await test_db_session.get(Session, session_id)
    ledger_rows = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalars().all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert refreshed.token_usage == {
        "total_input_tokens": 13,
        "total_output_tokens": 8,
        "per_node": _token_usage(input_tokens=13, output_tokens=8),
    }
    assert len(ledger_rows) == 1
    assert ledger_rows[0].billing_state == GenerationBillingState.CHARGE_FAILED
    assert ledger_rows[0].provider == GenerationBillingProvider.STRIPE
    assert ledger_rows[0].provider_error_code == "provider_timeout"
    assert ledger_rows[0].provider_error_message == "provider timed out after finalize"
    assert ledger_rows[0].charge_attempted_at == datetime(2026, 4, 14, 22, 0, 0)


@pytest.mark.asyncio
async def test_finalise_session_uses_existing_subscription_credit_when_present(test_db_session, test_user_id):
    session_id = uuid.uuid4()
    user = await test_db_session.get(UserProfile, test_user_id)
    if user is None:
        raise AssertionError("expected fixture user")
    await _grant_subscription_credit(test_db_session, user_id=test_user_id)

    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json={"free_text": "subscription path"},
    )
    test_db_session.add(session)
    await test_db_session.commit()

    await finalise_session(
        session_id,
        {
            "schedule": _schedule_payload("Subscription dinner"),
            "validated_recipes": [],
            "errors": [],
            "token_usage": _token_usage(input_tokens=7, output_tokens=5),
        },
        test_db_session,
    )

    refreshed = await test_db_session.get(Session, session_id)
    billing_rows = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalars().all()
    funding_entries = (
        await test_db_session.execute(
            select(GenerationFundingLedgerEntry).where(GenerationFundingLedgerEntry.session_id == session_id)
        )
    ).scalars().all()
    user_credit = (
        await test_db_session.execute(
            select(GenerationFundingGrant).where(GenerationFundingGrant.user_id == test_user_id)
        )
    ).scalar_one()

    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert len(billing_rows) == 1
    assert billing_rows[0].billing_state == GenerationBillingState.CHARGED
    assert billing_rows[0].billing_source_type == "subscription_credit"
    assert len(funding_entries) == 1
    assert funding_entries[0].funding_source_type == "subscription_credit"
    assert user_credit.remaining_amount == 0
    assert user_credit.grant_state == GenerationFundingGrantState.EXHAUSTED


@pytest.mark.asyncio
async def test_finalise_session_failed_run_does_not_create_ledger_row(test_db_session, test_user_id):
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json={"free_text": "sad path"},
    )
    test_db_session.add(session)
    await test_db_session.commit()

    await finalise_session(
        session_id,
        {
            "schedule": None,
            "validated_recipes": [],
            "errors": [{"node_name": "renderer", "message": "boom"}],
            "token_usage": [{"input_tokens": 5, "output_tokens": 1, "node_name": "renderer"}],
        },
        test_db_session,
    )

    refreshed = await test_db_session.get(Session, session_id)
    ledger_rows = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalars().all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.FAILED
    assert refreshed.error_summary == "renderer: boom"
    assert refreshed.token_usage["total_input_tokens"] == 5
    assert refreshed.token_usage["total_output_tokens"] == 1
    assert ledger_rows == []


@pytest.mark.asyncio
async def test_finalise_session_marks_partial_and_records_ledger_once(test_db_session, test_user_id):
    session_id = uuid.uuid4()
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.GENERATING,
        concept_json={"free_text": "partial path"},
    )
    test_db_session.add(session)
    await test_db_session.commit()

    await finalise_session(
        session_id,
        {
            "schedule": _schedule_payload("Dinner mostly ready"),
            "validated_recipes": [],
            "errors": [{"node_name": "enricher", "message": "dropped dessert"}],
        },
        test_db_session,
    )

    refreshed = await test_db_session.get(Session, session_id)
    billing_rows = (
        await test_db_session.execute(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).scalars().all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.PARTIAL
    assert refreshed.error_summary == "enricher: dropped dessert"
    assert len(billing_rows) == 1
    assert billing_rows[0].session_status == SessionStatus.PARTIAL


@pytest.mark.asyncio
async def test_cancel_pipeline_leaves_terminal_session_unchanged(test_db_session, test_user_id):
    session_id = uuid.uuid4()
    completed_at = datetime(2026, 4, 8, 13, 30, 0)
    session = Session(
        session_id=session_id,
        user_id=test_user_id,
        status=SessionStatus.COMPLETE,
        concept_json={"free_text": "done"},
        completed_at=completed_at,
    )
    test_db_session.add(session)
    await test_db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await cancel_pipeline(session_id, test_db_session, _current_user(test_user_id))

    refreshed = await test_db_session.get(Session, session_id)
    assert exc_info.value.status_code == 409
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert refreshed.completed_at == completed_at
