import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.api.routes.sessions import cancel_pipeline
from app.core.status import finalise_session
from app.models.enums import SessionStatus
from app.models.session import Session
from app.models.user import GenerationBillingRecord, GenerationBillingState, UserProfile


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
        await test_db_session.exec(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.CANCELLED
    assert refreshed.schedule_summary is None
    assert refreshed.result_schedule is None
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
        await test_db_session.exec(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).all()
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
    assert ledger_rows[0].billing_state == GenerationBillingState.READY
    assert ledger_rows[0].session_status == SessionStatus.COMPLETE
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
        "token_usage": [{"input_tokens": 3, "output_tokens": 2, "node_name": "renderer"}],
    }

    await finalise_session(session_id, final_state, test_db_session)
    await finalise_session(session_id, final_state, test_db_session)

    refreshed = await test_db_session.get(Session, session_id)
    ledger_rows = (
        await test_db_session.exec(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert len(ledger_rows) == 1
    assert ledger_rows[0].billing_state == GenerationBillingState.READY


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
        await test_db_session.exec(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).all()
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
    ledger_rows = (
        await test_db_session.exec(
            select(GenerationBillingRecord).where(GenerationBillingRecord.session_id == session_id)
        )
    ).all()
    assert refreshed is not None
    assert refreshed.status == SessionStatus.PARTIAL
    assert refreshed.error_summary == "enricher: dropped dessert"
    assert len(ledger_rows) == 1
    assert ledger_rows[0].session_status == SessionStatus.PARTIAL
    assert ledger_rows[0].billing_state == GenerationBillingState.READY


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

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Session is complete, not in progress"

    refreshed = await test_db_session.get(Session, session_id)
    assert refreshed is not None
    assert refreshed.status == SessionStatus.COMPLETE
    assert refreshed.completed_at == completed_at
