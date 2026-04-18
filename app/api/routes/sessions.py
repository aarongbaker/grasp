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
from sqlalchemy import asc, desc, func
from sqlalchemy.ext.asyncio import AsyncSession as SAAsyncSession
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.core.rate_limit import create_session_limit, limiter, user_identity_or_ip_key
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
    SessionBillingSummary,
    SessionDetailResponse,
    SessionOutstandingBalanceSummary,
    SessionRunAcceptedResponse,
    SessionRunBlockedResponse,
)
from app.models.recipe import ValidatedRecipe
from app.models.session import Session
from app.models.scheduling import NaturalLanguageSchedule
from app.api.routes.catalog import resolve_catalog_cookbook_access
from app.services.generation_billing import GenerationBillingService

router = APIRouter(prefix="/sessions")


def _session_exec(db: DBSession | SAAsyncSession, statement):
    exec_method = getattr(db, "exec", None)
    if callable(exec_method):
        return exec_method(statement)
    return db.execute(statement)


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
        matches = [PlannerAuthoredResolutionMatch(recipe_id=record.recipe_id, title=record.title) for record in records]
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
            "title": authored_recipe.title,
        },
    }


async def _resolve_planner_authored_anchor(
    *,
    body: CreateSessionPlannerAuthoredAnchorRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
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
    db: DBSession,
) -> dict:
    catalog_summary = await resolve_catalog_cookbook_access(
        body.planner_catalog_cookbook.catalog_cookbook_id,
        current_user,
        db,
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


def _build_outstanding_balance_response(
    *, session: Session, outstanding_balance: SessionOutstandingBalanceSummary
) -> SessionDetailResponse:
    return SessionDetailResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        status=_session_status(session.status),
        concept_json=session.concept_json,
        schedule_summary=session.schedule_summary,
        total_duration_minutes=session.total_duration_minutes,
        error_summary=session.error_summary,
        result_recipes=session.result_recipes,
        result_schedule=session.result_schedule,
        token_usage=session.token_usage,
        celery_task_id=session.celery_task_id,
        created_at=session.created_at,
        started_at=session.started_at,
        completed_at=session.completed_at,
        billing=SessionBillingSummary(outstanding_balance=outstanding_balance),
    )


@router.post("/planner/resolve", response_model=PlannerReferenceResolutionResponse)
@limiter.limit("30/minute")
async def resolve_planner_reference(
    request: Request,
    body: PlannerReferenceResolutionRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    return await _resolve_planner_reference_matches(
        kind=body.kind,
        reference=body.reference,
        db=db,
        current_user=current_user,
    )


@router.post("", status_code=201)
@limiter.limit(create_session_limit, key_func=user_identity_or_ip_key)
async def create_session(request: Request, body: CreateSessionRequest, db: DBSession, current_user: CurrentUser):
    merged_restrictions = list(set(current_user.dietary_defaults + body.dietary_restrictions))

    concept_fields: dict = {
        "guest_count": body.guest_count,
        "dish_count": body.dish_count,
        "meal_type": body.meal_type,
        "occasion": body.occasion,
        "dietary_restrictions": merged_restrictions,
        "serving_time": body.serving_time,
    }

    if isinstance(body, CreateSessionAuthoredRequest):
        concept_fields.update(await _resolve_authored_selection(body=body, db=db, current_user=current_user))
    elif isinstance(body, CreateSessionPlannerAuthoredAnchorRequest):
        concept_fields.update(await _resolve_planner_authored_anchor(body=body, db=db, current_user=current_user))
    elif isinstance(body, CreateSessionPlannerCookbookTargetRequest):
        concept_fields.update(await _resolve_planner_cookbook_target(body=body, db=db, current_user=current_user))
    elif isinstance(body, CreateSessionPlannerCatalogCookbookRequest):
        concept_fields.update(await _resolve_planner_catalog_cookbook(body=body, current_user=current_user, db=db))
    elif isinstance(body, CreateSessionCookbookRequest):
        concept_fields.update(
            {
                "concept_source": body.concept_source,
                "free_text": body.free_text,
            }
        )
    else:
        concept_fields.update(
            {
                "concept_source": "free_text",
                "free_text": body.free_text,
            }
        )

    concept = DinnerConcept.model_validate(concept_fields)

    session = Session(
        user_id=current_user.user_id,
        status=SessionStatus.PENDING,
        concept_json=concept.model_dump(mode="json"),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.post(
    "/{session_id}/run", status_code=202, response_model=SessionRunAcceptedResponse | SessionRunBlockedResponse
)
@limiter.limit("5/minute")
async def run_pipeline(request: Request, session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    session_status = _session_status(session.status)
    if session_status != SessionStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Session is already {session_status.value}")

    billing_service = GenerationBillingService()
    gate = await billing_service.evaluate_run_gate(db, session=session, user=current_user)
    if not gate.can_run:
        return SessionRunBlockedResponse(
            session_id=session.session_id,
            status="blocked",
            reason_code="payment_method_required",
            message=gate.reason,
            requires_payment_method=True,
            next_action={
                "kind": "update_payment_method",
                "label": "Add payment method",
                "session_id": session.session_id,
            },
        )

    from app.workers.tasks import run_grasp_pipeline

    kickoff_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    kickoff_failure_summary = "Pipeline kickoff failed before execution was accepted. Please retry."

    # Broker acceptance is the causal boundary for durable GENERATING. We only
    # write GENERATING/started_at/celery_task_id as one logical transition after
    # Celery returns an accepted task id. If enqueue fails, keep the row retryable
    # as PENDING with a user-visible error_summary instead of stranding GENERATING.
    try:
        result = run_grasp_pipeline.delay(str(session_id), str(current_user.user_id))  # type: ignore[attr-defined]
    except Exception as exc:
        session.status = SessionStatus.PENDING
        session.started_at = None
        session.celery_task_id = None
        session.error_summary = kickoff_failure_summary
        db.add(session)
        await db.commit()
        raise HTTPException(status_code=503, detail=kickoff_failure_summary) from exc

    session.status = SessionStatus.GENERATING
    session.started_at = kickoff_started_at
    session.celery_task_id = result.id
    session.error_summary = None
    db.add(session)

    try:
        await db.commit()
    except Exception as exc:
        try:
            from app.workers.celery_app import celery_app

            celery_app.control.revoke(result.id, terminate=True, signal="SIGTERM")
        except Exception:
            pass

        session.status = SessionStatus.PENDING
        session.started_at = None
        session.celery_task_id = None
        session.error_summary = kickoff_failure_summary
        db.add(session)
        await db.rollback()
        await db.commit()
        raise HTTPException(status_code=503, detail=kickoff_failure_summary) from exc

    return SessionRunAcceptedResponse(
        session_id=session_id,
        status="generating",
        message="Pipeline enqueued",
    )


@router.post("/{session_id}/cancel", status_code=200)
async def cancel_pipeline(session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    stmt = (
        select(Session)
        .where(Session.session_id == session_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    result = await _session_exec(db, stmt)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        await db.rollback()
        raise HTTPException(status_code=403, detail="Access denied")
    session_status = _session_status(session.status)
    if session_status == SessionStatus.CANCELLED:
        await db.rollback()
        return {"session_id": str(session_id), "status": "cancelled"}
    if not session_status.is_in_progress:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Session is {session_status.value}, not in progress")

    if session.celery_task_id:
        try:
            from app.workers.celery_app import celery_app

            celery_app.control.revoke(session.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception:
            pass

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


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session_status(session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    billing_service = GenerationBillingService()
    outstanding = await billing_service.get_outstanding_balance_status(db, session=session)
    outstanding_summary = SessionOutstandingBalanceSummary(
        has_outstanding_balance=outstanding.has_outstanding_balance,
        can_retry_charge=outstanding.can_retry_charge,
        billing_state=(
            outstanding.billing_state.value
            if hasattr(outstanding.billing_state, "value")
            else outstanding.billing_state
        )
        if outstanding.billing_state
        else None,
        reason_code=outstanding.reason_code,
        reason=outstanding.reason,
        retry_attempted_at=outstanding.retry_attempted_at,
        recovery_action={
            "kind": "retry_outstanding_balance",
            "label": "Resolve outstanding balance",
            "session_id": session.session_id,
        }
        if outstanding.can_retry_charge
        else None,
    )

    session_status = _session_status(session.status)
    if session_status.is_terminal:
        return _build_outstanding_balance_response(session=session, outstanding_balance=outstanding_summary)

    if session_status.is_in_progress:
        from app.core.status import status_projection
        from app.main import get_graph

        try:
            graph = await get_graph()
            live_status = await status_projection(session_id, graph)
            return _build_outstanding_balance_response(
                session=Session.model_validate({**session.model_dump(), "status": live_status}),
                outstanding_balance=outstanding_summary,
            )
        except Exception:
            return _build_outstanding_balance_response(session=session, outstanding_balance=outstanding_summary)

    return _build_outstanding_balance_response(session=session, outstanding_balance=outstanding_summary)


@router.get("/{session_id}/results")
async def get_session_results(session_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
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

    if session.result_schedule and session.result_recipes is not None:
        persisted_schedule = NaturalLanguageSchedule.model_validate(session.result_schedule)
        persisted_recipes = [ValidatedRecipe.model_validate(recipe) for recipe in session.result_recipes]
        return {
            "schedule": persisted_schedule.model_dump(mode="json"),
            "recipes": [recipe.model_dump(mode="json") for recipe in persisted_recipes],
            "errors": [],
        }

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
