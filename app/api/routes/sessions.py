"""
api/routes/sessions.py — Session creation, pipeline enqueue, status polling.

Two-tier read in GET /sessions/{id}:
  terminal status → read Session row directly (fast, indexed)
  in-progress     → call status_projection() from checkpoint

POST /sessions/{id}/run is the ONLY place GENERATING is written to DB.
All other status transitions are handled by finalise_session() or derived
by status_projection(). This is the V1.6 single-source-of-truth contract.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.models.enums import SessionStatus
from app.models.ingestion import BookRecord, CookbookChunk
from app.models.pipeline import (
    CreateSessionCookbookRequest,
    CreateSessionLegacyRequest,
    DinnerConcept,
    SelectedCookbookRecipe,
)
from app.models.session import Session

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/sessions")


def _session_status(value: SessionStatus | str) -> SessionStatus:
    return value if isinstance(value, SessionStatus) else SessionStatus(value)


def _summarise_selected_recipes(recipes: list[SelectedCookbookRecipe]) -> str:
    names = [recipe.text.strip() for recipe in recipes if recipe.text.strip()]
    preview = ", ".join(names[:3])
    if len(names) > 3:
        preview += f", and {len(names) - 3} more"
    return f"Cookbook-selected recipes: {preview}." if preview else "Cookbook-selected recipes."


async def _load_authorized_selected_recipes(
    db: DBSession,
    current_user: CurrentUser,
    chunk_ids: list[uuid.UUID],
) -> list[SelectedCookbookRecipe]:
    statement = (
        select(CookbookChunk, BookRecord)
        .join(BookRecord, CookbookChunk.book_id == BookRecord.book_id)
        .where(
            CookbookChunk.chunk_id.in_(chunk_ids),
            CookbookChunk.user_id == current_user.user_id,
            BookRecord.user_id == current_user.user_id,
        )
    )
    results = await db.exec(statement)
    rows = results.all()

    owned_by_chunk_id = {
        chunk.chunk_id: SelectedCookbookRecipe(
            chunk_id=chunk.chunk_id,
            book_id=book.book_id,
            book_title=book.title,
            text=chunk.text,
            chapter=chunk.chapter,
            page_number=chunk.page_number,
        )
        for chunk, book in rows
    }

    missing = [str(chunk_id) for chunk_id in chunk_ids if chunk_id not in owned_by_chunk_id]
    if missing:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "One or more selected cookbook recipes were not found for the current user.",
                "invalid_chunk_ids": missing,
            },
        )

    return [owned_by_chunk_id[chunk_id] for chunk_id in chunk_ids]


@router.post("", status_code=201)
@limiter.limit("30/minute")
async def create_session(request: Request, body: CreateSessionLegacyRequest | CreateSessionCookbookRequest, db: DBSession, current_user: CurrentUser):
    # Merge chef's dietary_defaults into every session automatically
    merged_restrictions = list(set(current_user.dietary_defaults + body.dietary_restrictions))

    if isinstance(body, CreateSessionCookbookRequest):
        selected_recipes = await _load_authorized_selected_recipes(
            db,
            current_user,
            [selection.chunk_id for selection in body.selected_recipes],
        )
        concept = DinnerConcept(
            free_text=_summarise_selected_recipes(selected_recipes),
            guest_count=body.guest_count,
            meal_type=body.meal_type,
            occasion=body.occasion,
            dietary_restrictions=merged_restrictions,
            serving_time=body.serving_time,
            concept_source="cookbook",
            selected_recipes=selected_recipes,
        )
    else:
        concept = DinnerConcept(
            free_text=body.free_text,
            guest_count=body.guest_count,
            meal_type=body.meal_type,
            occasion=body.occasion,
            dietary_restrictions=merged_restrictions,
            serving_time=body.serving_time,
        )

    session = Session(
        user_id=current_user.user_id,
        status=SessionStatus.PENDING,
        concept_json=concept.model_dump(mode="json"),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.post("/{session_id}/run", status_code=202)
@limiter.limit("5/minute")
async def run_pipeline(request: Request, session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """
    Enqueues the LangGraph pipeline as a Celery task.
    Returns 202 immediately — does NOT wait for pipeline completion.

    This is the ONLY place that writes GENERATING to Session.status.
    V1.6 state ownership contract: two writers, no more.
    """
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    session_status = _session_status(session.status)
    if session_status != SessionStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Session is already {session.status}")

    # Direct DB write — the one exception to the checkpoint-derived rule
    session.status = SessionStatus.GENERATING
    session.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(session)
    await db.commit()

    # Enqueue Celery task and store task ID for cancellation
    from app.workers.tasks import run_grasp_pipeline

    result = run_grasp_pipeline.delay(str(session_id), str(current_user.user_id))
    session.celery_task_id = result.id
    db.add(session)
    await db.commit()

    return {"session_id": str(session_id), "status": "generating", "message": "Pipeline enqueued"}


@router.post("/{session_id}/cancel", status_code=200)
async def cancel_pipeline(session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """
    Cancels an in-progress pipeline by revoking its Celery task and
    marking the session as CANCELLED. Idempotent — returns 200 if already cancelled.
    """
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    session_status = _session_status(session.status)
    if session_status == SessionStatus.CANCELLED:
        return {"session_id": str(session_id), "status": "cancelled"}
    if not session_status.is_in_progress:
        raise HTTPException(status_code=409, detail=f"Session is {session.status}, not in progress")

    # Revoke the Celery task (best-effort — don't fail cancel if revoke fails)
    if session.celery_task_id:
        try:
            from app.workers.celery_app import celery_app

            celery_app.control.revoke(session.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception:
            pass  # Task may already be done; cancellation still marks status

    session.status = SessionStatus.CANCELLED
    session.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(session)
    await db.commit()

    return {"session_id": str(session_id), "status": "cancelled"}


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    await db.delete(session)
    await db.commit()


@router.get("/{session_id}")
async def get_session_status(session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """
    Two-tier read (§2.9):
      terminal status → return Session row directly
      in-progress     → derive status from LangGraph checkpoint via status_projection()
    """
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    session_status = _session_status(session.status)
    if session_status.is_terminal:
        # Fast path: read the DB row directly
        return session

    if session_status.is_in_progress:
        # Slow path: derive live status from checkpoint
        from app.core.status import status_projection
        from app.main import get_graph  # injected at startup

        try:
            graph = await get_graph()
            live_status = await status_projection(session_id, graph)
            return {**session.model_dump(), "status": live_status}
        except Exception:
            # Fall back to DB row if checkpoint unavailable
            return session

    return session


@router.get("/{session_id}/results")
async def get_session_results(session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """
    Returns the full pipeline output (schedule, recipes, errors) for a terminal session.
    Reads from the LangGraph checkpoint — only callable when session is complete/partial.
    """
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    session_status = _session_status(session.status)
    if not session_status.is_terminal:
        raise HTTPException(status_code=409, detail="Session is not yet complete")
    if session_status == SessionStatus.FAILED:
        raise HTTPException(status_code=409, detail="Session failed — no results available")

    # Fast path: read from persisted columns (populated by finalise_session)
    if session.result_schedule and session.result_recipes is not None:
        return {
            "schedule": session.result_schedule,
            "recipes": session.result_recipes,
            "errors": [],
        }

    # Slow path (backward compat for sessions finalized before migration)
    from app.main import get_graph

    graph = await get_graph()
    config = {"configurable": {"thread_id": str(session_id)}}

    try:
        state_snapshot = await graph.aget_state(config)
        state = state_snapshot.values if state_snapshot else {}
    except Exception:
        raise HTTPException(status_code=502, detail="Could not read pipeline state from checkpoint")

    if not state:
        raise HTTPException(status_code=502, detail="Checkpoint state is empty")

    from app.models.recipe import ValidatedRecipe
    from app.models.scheduling import NaturalLanguageSchedule

    schedule_dict = state.get("schedule")
    if not schedule_dict:
        raise HTTPException(status_code=502, detail="No schedule found in pipeline state")

    schedule = NaturalLanguageSchedule.model_validate(schedule_dict)
    recipes = [ValidatedRecipe.model_validate(r) for r in state.get("validated_recipes", [])]
    errors = state.get("errors", [])

    return {
        "schedule": schedule.model_dump(),
        "recipes": [r.model_dump() for r in recipes],
        "errors": errors,
    }
