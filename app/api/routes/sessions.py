"""
api/routes/sessions.py — Session creation, pipeline enqueue, status polling.

Two-tier read in GET /sessions/{id}:
  terminal status → read Session row directly (fast, indexed)
  in-progress     → call status_projection() from checkpoint

Why two tiers?
  LangGraph checkpoints are optimised for graph resumption, not key-value reads.
  Reading the checkpoint for every status poll adds 10-50ms of latency and
  requires an active graph instance. Once a session reaches a terminal state
  (COMPLETE, FAILED, CANCELLED, PARTIAL), it never changes again — the DB row
  is the definitive source. Only in-progress sessions need the checkpoint read.

POST /sessions/{id}/run is the ONLY place GENERATING is written to DB.
All other status transitions are handled by finalise_session() or derived
by status_projection(). This is the V1.6 single-source-of-truth contract.

Cancellation: uses SELECT FOR UPDATE to prevent the cancel route and
finalise_session() from racing. The for-update lock serializes them.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import asc, desc, func
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.core.rate_limit import create_session_limit, user_identity_or_ip_key
from app.models.authored_recipe import AuthoredRecipeRecord, RecipeCookbookRecord
from app.models.catalog import CatalogCookbookAccessState
from app.models.enums import MealType, Occasion, SessionStatus
from app.models.pipeline import (
    CreateSessionAuthoredRequest,
    CreateSessionCookbookRequest,
    CreateSessionLegacyRequest,
    CreateSessionPlannerAuthoredAnchorRequest,
    CreateSessionPlannerCatalogCookbookRequest,
    CreateSessionPlannerCookbookTargetRequest,
    CreateSessionRequest,
    DinnerConcept,
    PlannerAuthoredResolutionMatch,
    PlannerCookbookResolutionMatch,
    PlannerReferenceKind,
    PlannerReferenceResolutionRequest,
    PlannerReferenceResolutionResponse,
    PlannerResolutionMatch,
    PlannerResolutionMatchStatus,
)
from app.models.recipe import ValidatedRecipe
from app.models.session import Session
from app.models.scheduling import NaturalLanguageSchedule
from app.api.routes.catalog import resolve_catalog_cookbook_access

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/sessions")


def _session_status(value: SessionStatus | str) -> SessionStatus:
    """Coerce a raw status value to SessionStatus enum.

    Session.status is stored as a string in the DB (SQLModel enum column).
    On read it may come back as a string or already-coerced enum depending
    on SQLModel version and ORM session state. This helper normalizes both.
    """
    return value if isinstance(value, SessionStatus) else SessionStatus(value)


def _normalize_reference_search(value: str) -> str:
    """Normalize a reference search string for case-insensitive LIKE query."""
    return value.strip().lower()


async def _resolve_planner_reference_matches(
    *,
    kind: PlannerReferenceKind,
    reference: str,
    db: DBSession,
    current_user: CurrentUser,
) -> PlannerReferenceResolutionResponse:
    """Resolve a planner reference string to matching authored recipes or cookbooks.

    Uses a case-insensitive LIKE query with % wildcards so partial name matches
    work (e.g. "pasta" matches "Pasta al Limone"). Results are sorted by
    updated_at DESC so the most recently edited item appears first.

    Returns RESOLVED if exactly 1 match, AMBIGUOUS if multiple, NO_MATCH if zero.
    The frontend uses this to prompt the user to clarify before creating a session.
    """
    normalized_reference = _normalize_reference_search(reference)
    search_term = f"%{normalized_reference}%"

    matches: list[PlannerResolutionMatch]
    if kind == PlannerReferenceKind.AUTHORED:
        stmt = (
            select(AuthoredRecipeRecord)
            .where(AuthoredRecipeRecord.user_id == current_user.user_id)
            .where(func.lower(AuthoredRecipeRecord.title).like(search_term))
            .order_by(desc(AuthoredRecipeRecord.updated_at), asc(AuthoredRecipeRecord.title))  # type: ignore[arg-type]
        )
        records = (await db.exec(stmt)).all()
        matches = [
            PlannerAuthoredResolutionMatch(recipe_id=record.recipe_id, title=record.title)
            for record in records
        ]
    else:
        stmt = (
            select(RecipeCookbookRecord)
            .where(RecipeCookbookRecord.user_id == current_user.user_id)
            .where(func.lower(RecipeCookbookRecord.name).like(search_term))
            .order_by(desc(RecipeCookbookRecord.updated_at), asc(RecipeCookbookRecord.name))  # type: ignore[arg-type]
        )
        records = (await db.exec(stmt)).all()
        matches = [
            PlannerCookbookResolutionMatch(
                cookbook_id=record.cookbook_id,
                name=record.name,
                description=record.description,
            )
            for record in records
        ]

    if not matches:
        status = PlannerResolutionMatchStatus.NO_MATCH
    elif len(matches) == 1:
        status = PlannerResolutionMatchStatus.RESOLVED
    else:
        status = PlannerResolutionMatchStatus.AMBIGUOUS

    return PlannerReferenceResolutionResponse(
        kind=kind,
        reference=reference.strip(),
        status=status,
        matches=matches,
    )


async def _resolve_authored_selection(
    *,
    body: CreateSessionAuthoredRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """Verify the authored recipe exists and belongs to current_user.

    Returns a concept_fields dict fragment with the resolved authored recipe data.
    We re-read the title from the DB rather than trusting the request body —
    the client may have sent a stale title if the recipe was renamed since selection.
    """
    authored_recipe = await db.get(AuthoredRecipeRecord, body.selected_authored_recipe.recipe_id)
    if authored_recipe is None:
        raise HTTPException(status_code=404, detail="Authored recipe not found")
    if authored_recipe.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return {
        "concept_source": "authored",
        "free_text": body.free_text,
        "selected_authored_recipe": {
            "recipe_id": str(authored_recipe.recipe_id),
            "title": authored_recipe.title,  # Canonical title from DB, not request
        },
    }


async def _resolve_planner_authored_anchor(
    *,
    body: CreateSessionPlannerAuthoredAnchorRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """Verify the planner authored anchor exists and belongs to current_user."""
    authored_recipe = await db.get(AuthoredRecipeRecord, body.planner_authored_recipe_anchor.recipe_id)
    if authored_recipe is None:
        raise HTTPException(status_code=404, detail="Authored recipe not found")
    if authored_recipe.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return {
        "concept_source": "planner_authored_anchor",
        "free_text": body.free_text,
        "planner_authored_recipe_anchor": {
            "recipe_id": str(authored_recipe.recipe_id),
            "title": authored_recipe.title,
        },
    }


async def _resolve_planner_cookbook_target(
    *,
    body: CreateSessionPlannerCookbookTargetRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """Verify the planner cookbook target exists and belongs to current_user."""
    cookbook = await db.get(RecipeCookbookRecord, body.planner_cookbook_target.cookbook_id)
    if cookbook is None:
        raise HTTPException(status_code=404, detail="Recipe cookbook not found")
    if cookbook.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return {
        "concept_source": "planner_cookbook_target",
        "free_text": body.free_text,
        "planner_cookbook_target": {
            "cookbook_id": str(cookbook.cookbook_id),
            "name": cookbook.name,
            "description": cookbook.description,
            "mode": body.planner_cookbook_target.mode.value,
        },
    }


async def _resolve_planner_catalog_cookbook(
    *,
    body: CreateSessionPlannerCatalogCookbookRequest,
    current_user: CurrentUser,
) -> dict:
    """Resolve one catalog cookbook through the backend entitlement seam.

    Persist canonical catalog metadata from the backend fixture seam rather than
    trusting client-supplied title/access fields. Preview and included catalog
    cookbooks are both planner-selectable; locked catalog items fail explicitly.
    """
    catalog_summary = resolve_catalog_cookbook_access(
        body.planner_catalog_cookbook.catalog_cookbook_id,
        current_user,
    )
    if catalog_summary.access_state == CatalogCookbookAccessState.LOCKED:
        raise HTTPException(status_code=403, detail=catalog_summary.access_state_reason)

    return {
        "concept_source": "planner_catalog_cookbook",
        "free_text": body.free_text,
        "planner_catalog_cookbook": {
            "catalog_cookbook_id": str(catalog_summary.catalog_cookbook_id),
            "slug": catalog_summary.slug,
            "title": catalog_summary.title,
            "access_state": catalog_summary.access_state.value,
            "access_state_reason": catalog_summary.access_state_reason,
        },
    }


@router.post("/planner/resolve", response_model=PlannerReferenceResolutionResponse)
@limiter.limit("30/minute")
async def resolve_planner_reference(
    request: Request,
    body: PlannerReferenceResolutionRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    """Resolve a planner reference string to matching library items.

    Used by the planner UI to resolve natural-language references to specific
    authored recipes or cookbooks before session creation. The frontend calls
    this endpoint, shows the matches, lets the user confirm, then calls POST /sessions
    with the exact recipe_id or cookbook_id from the resolved match.
    """
    return await _resolve_planner_reference_matches(
        kind=body.kind,
        reference=body.reference,
        db=db,
        current_user=current_user,
    )


@router.post("", status_code=201)
@limiter.limit(create_session_limit, key_func=user_identity_or_ip_key)
async def create_session(request: Request, body: CreateSessionRequest, db: DBSession, current_user: CurrentUser):
    """Create a new session with PENDING status.

    Does NOT start the pipeline — call POST /sessions/{id}/run after creation.
    Separating creation from enqueue allows the frontend to show a confirmation
    screen before committing to a potentially expensive LLM pipeline run.

    Dietary defaults from the user's profile are merged with the request's
    dietary_restrictions using set union — the chef doesn't have to re-specify
    their dietary defaults every session. Duplicates are removed.

    concept_fields is built progressively: shared fields first, then
    source-specific fields from the resolution helpers. DinnerConcept.model_validate()
    runs validate_source_contract to catch any misconfiguration.
    """
    # Merge chef's dietary_defaults into every session automatically.
    # set() deduplicates — "vegan" from profile + "vegan" from request → ["vegan"].
    merged_restrictions = list(set(current_user.dietary_defaults + body.dietary_restrictions))

    concept_fields: dict = {
        "guest_count": body.guest_count,
        "dish_count": body.dish_count,
        "meal_type": body.meal_type,
        "occasion": body.occasion,
        "dietary_restrictions": merged_restrictions,
        "serving_time": body.serving_time,
    }

    # Source-specific fields are resolved and merged into concept_fields.
    # Each _resolve_* function validates ownership and returns a dict fragment.
    if isinstance(body, CreateSessionAuthoredRequest):
        concept_fields.update(await _resolve_authored_selection(body=body, db=db, current_user=current_user))
    elif isinstance(body, CreateSessionPlannerAuthoredAnchorRequest):
        concept_fields.update(await _resolve_planner_authored_anchor(body=body, db=db, current_user=current_user))
    elif isinstance(body, CreateSessionPlannerCookbookTargetRequest):
        concept_fields.update(await _resolve_planner_cookbook_target(body=body, db=db, current_user=current_user))
    elif isinstance(body, CreateSessionPlannerCatalogCookbookRequest):
        concept_fields.update(await _resolve_planner_catalog_cookbook(body=body, current_user=current_user))
    elif isinstance(body, CreateSessionCookbookRequest):
        # Cookbook mode: selected_recipes will be resolved by the generator node
        # at pipeline start. For now, store chunk_ids in the concept.
        concept_fields.update(
            {
                "concept_source": body.concept_source,
                "free_text": body.free_text,
            }
        )
    else:
        # Legacy free_text mode
        concept_fields.update(
            {
                "concept_source": "free_text",
                "free_text": body.free_text,
            }
        )

    # model_validate() runs the full validate_source_contract cross-field validator.
    # If concept_fields is inconsistent, this raises ValidationError → HTTP 422.
    concept = DinnerConcept.model_validate(concept_fields)

    session = Session(
        user_id=current_user.user_id,
        status=SessionStatus.PENDING,
        # concept_json is the authoritative source for the pipeline.
        # Stored as a plain dict — no Pydantic dependency on read.
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
      Writer 1 (this route): PENDING → GENERATING
      Writer 2 (finalise_session): GENERATING → COMPLETE/FAILED/PARTIAL

    Idempotency guard: returns 409 if the session is already in progress
    or terminal. The frontend should not call /run twice, but this guard
    prevents double-billing if it does.

    celery_task_id is stored so POST /sessions/{id}/cancel can revoke the task.
    """
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    session_status = _session_status(session.status)
    if session_status != SessionStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Session is already {session_status.value}")

    # Direct DB write — the one exception to the checkpoint-derived rule.
    # GENERATING is the only status written by the API server (not finalise_session).
    session.status = SessionStatus.GENERATING
    session.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(session)
    await db.commit()

    # Enqueue Celery task and store task ID for cancellation.
    # The task ID is needed by POST /cancel to revoke the task via Celery's
    # control interface. Without it, cancellation can only set the DB status
    # but cannot stop the already-running Celery worker.
    from app.workers.tasks import run_grasp_pipeline

    result = run_grasp_pipeline.delay(str(session_id), str(current_user.user_id))  # type: ignore[attr-defined]
    session.celery_task_id = result.id
    db.add(session)
    await db.commit()

    return {"session_id": str(session_id), "status": "generating", "message": "Pipeline enqueued"}


@router.post("/{session_id}/cancel", status_code=200)
async def cancel_pipeline(session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """
    Cancels an in-progress pipeline by revoking its Celery task and
    marking the session as CANCELLED. Idempotent — returns 200 if already cancelled.

    Uses SELECT FOR UPDATE to prevent a race condition with finalise_session():
    if the pipeline completes at the same moment as the cancel request,
    the lock ensures only one writer wins. The CANCELLED status guard in
    finalise_session() ensures it respects the cancellation.

    Task revocation: best-effort — if the Celery task has already completed
    or the broker is unavailable, revoke() will fail silently. The DB status
    is still written to CANCELLED regardless, preventing any future /run calls.
    """
    stmt = (
        select(Session)
        .where(Session.session_id == session_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    result = await db.exec(stmt)
    session = result.first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        await db.rollback()
        raise HTTPException(status_code=403, detail="Access denied")
    session_status = _session_status(session.status)
    if session_status == SessionStatus.CANCELLED:
        # Idempotent — already cancelled, return success without re-writing.
        await db.rollback()
        return {"session_id": str(session_id), "status": "cancelled"}
    if not session_status.is_in_progress:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Session is {session_status.value}, not in progress")

    # Revoke the Celery task (best-effort — don't fail cancel if revoke fails).
    # SIGTERM allows the task to do cleanup before stopping. If the task has
    # already committed its final state, revoke is a no-op.
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
    """Delete a session and its DB record. Does not affect the LangGraph checkpoint.

    The checkpoint is not deleted here — checkpoint cleanup is out-of-scope for V1.
    Stale checkpoints in Postgres don't cause correctness issues because each
    session has its own thread_id namespace.
    """
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
      terminal status → return Session row directly (fast path)
      in-progress     → derive status from LangGraph checkpoint via status_projection()

    The fast path is important for performance: once a session completes,
    all subsequent reads serve the DB row without touching the checkpoint.
    This is especially important when the UI refreshes a completed session.

    The slow path (status_projection) reads the live checkpoint to derive
    the current pipeline stage (GENERATING → ENRICHING → VALIDATING → SCHEDULING).
    On checkpoint failure, falls back to the DB row (showing GENERATING).
    """
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    session_status = _session_status(session.status)
    if session_status.is_terminal:
        # Fast path: read the DB row directly — terminal status never changes.
        return session

    if session_status.is_in_progress:
        # Slow path: derive live status from checkpoint.
        # status_projection() uses graph.aget_state() to read the latest
        # checkpoint without acquiring a write lock.
        from app.core.status import status_projection
        from app.main import get_graph  # injected at startup

        try:
            graph = await get_graph()
            live_status = await status_projection(session_id, graph)
            # Return the session dict with the live status overriding the DB status.
            # The DB still shows GENERATING; only this response shows the live stage.
            return {**session.model_dump(), "status": live_status}
        except Exception:
            # Fall back to DB row if checkpoint unavailable.
            # This happens during graph initialization or Postgres downtime.
            return session

    return session


@router.get("/{session_id}/results")
async def get_session_results(session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """
    Returns the full pipeline output (schedule, recipes, errors) for a terminal session.

    Two paths:
      Fast path: read result_schedule + result_recipes from Session columns.
        Populated by finalise_session() when the pipeline completes.
        Avoids checkpoint lookup — suitable for high-frequency polling.
      Slow path (backward compat): read from LangGraph checkpoint.
        Used for sessions finalised before the result_schedule column existed.
        Will be removed in a future migration.

    Only callable when session is COMPLETE or PARTIAL — returns 409 otherwise.
    FAILED sessions have no schedule to return.
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

    # Fast path: read from persisted columns (populated by finalise_session).
    # model_validate() re-parses the stored JSON to validate and provide type safety.
    if session.result_schedule and session.result_recipes is not None:
        persisted_schedule = NaturalLanguageSchedule.model_validate(session.result_schedule)
        persisted_recipes = [ValidatedRecipe.model_validate(recipe) for recipe in session.result_recipes]
        return {
            "schedule": persisted_schedule.model_dump(mode="json"),
            "recipes": [recipe.model_dump(mode="json") for recipe in persisted_recipes],
            "errors": [],
        }

    # Slow path (backward compat for sessions finalized before migration).
    # Read from checkpoint — requires the graph to be initialised.
    from app.main import get_graph

    graph = await get_graph()
    config = {"configurable": {"thread_id": str(session_id)}}

    try:
        state_snapshot = await graph.aget_state(config)  # type: ignore[attr-defined]
        state = state_snapshot.values if state_snapshot else {}
    except Exception:
        raise HTTPException(status_code=502, detail="Could not read pipeline state from checkpoint")

    if not state:
        raise HTTPException(status_code=502, detail="Checkpoint state is empty")

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
