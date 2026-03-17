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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlmodel import select

from core.deps import CurrentUser, DBSession
from models.enums import MealType, Occasion, SessionStatus
from models.pipeline import DinnerConcept
from models.session import Session

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/sessions")


class CreateSessionRequest(BaseModel):
    free_text: str = Field(max_length=2000)
    guest_count: int = Field(ge=1, le=100)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []


@router.post("", status_code=201)
@limiter.limit("30/minute")
async def create_session(request: Request, body: CreateSessionRequest, db: DBSession, current_user: CurrentUser):
    # Merge chef's dietary_defaults into every session automatically
    merged_restrictions = list(set(current_user.dietary_defaults + body.dietary_restrictions))

    concept = DinnerConcept(
        free_text=body.free_text,
        guest_count=body.guest_count,
        meal_type=body.meal_type,
        occasion=body.occasion,
        dietary_restrictions=merged_restrictions,
    )

    session = Session(
        user_id=current_user.user_id,
        status=SessionStatus.PENDING,
        concept_json=concept.model_dump(),
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
    if session.status != SessionStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Session is already {session.status}")

    # Direct DB write — the one exception to the checkpoint-derived rule
    session.status = SessionStatus.GENERATING
    session.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(session)
    await db.commit()

    # Enqueue Celery task
    from workers.tasks import run_grasp_pipeline

    run_grasp_pipeline.delay(str(session_id), str(current_user.user_id))

    return {"session_id": str(session_id), "status": "generating", "message": "Pipeline enqueued"}


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

    if session.status.is_terminal:
        # Fast path: read the DB row directly
        return session

    if session.status.is_in_progress:
        # Slow path: derive live status from checkpoint
        from core.status import status_projection
        from main import get_graph  # injected at startup

        try:
            graph = get_graph()
            live_status = await status_projection(session_id, graph)
            return {**session.model_dump(), "status": live_status}
        except Exception:
            # Fall back to DB row if checkpoint unavailable
            return session

    return session
