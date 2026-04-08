"""
core/status.py
finalise_session() and status_projection() — the two functions that
manage Session.status as a read-optimised projection of the checkpoint.

finalise_session():
  Called ONCE when the pipeline ends (by Celery task wrapper in production,
  explicitly in tests). Reads terminal GRASPState from checkpoint. Writes
  terminal status, schedule_summary, total_duration_minutes, error_summary,
  completed_at to Session row.

status_projection():
  Called by GET /sessions/{id} for in-progress sessions. Reads live
  GRASPState from checkpoint. Derives SessionStatus from what fields
  are populated — no node name tracking needed.

  Status derivation rules (most-advanced state wins):
    schedule populated          → shouldn't reach here (terminal)
    merged_dag OR recipe_dags   → SCHEDULING  (dag_builder or dag_merger ran)
    validated_recipes populated → SCHEDULING   (validator ran, awaiting dag_builder)
    enriched_recipes populated  → VALIDATING
    raw_recipes populated       → ENRICHING
    else                        → GENERATING (pipeline started, generator running)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.enums import SessionStatus
from app.models.errors import NodeError
from app.models.recipe import ValidatedRecipe
from app.models.scheduling import NaturalLanguageSchedule
from app.models.session import Session


async def finalise_session(
    session_id: uuid.UUID,
    final_state: dict,
    db: AsyncSession,
) -> None:
    """
    Writes terminal state to the Session row. Called exactly once per pipeline run.
    final_state is the GRASPState dict returned by graph.ainvoke().
    """
    stmt = (
        select(Session)
        .where(Session.session_id == session_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    result = (await db.execute(stmt)).scalar_one_or_none()
    if not result:
        return

    # If the session was cancelled while the pipeline was running, don't overwrite
    if result.status == SessionStatus.CANCELLED:
        await db.rollback()
        return

    errors: list[dict] = final_state.get("errors", [])
    has_errors = len(errors) > 0

    schedule_dict = final_state.get("schedule")
    if schedule_dict:
        schedule = NaturalLanguageSchedule.model_validate(schedule_dict)
        result.schedule_summary = schedule.summary
        result.total_duration_minutes = schedule.total_duration_minutes
        result.status = SessionStatus.COMPLETE
        # Persist full results for fast reads (avoids checkpoint lookups).
        # Serialize through Pydantic to ensure JSON-safe types (e.g. datetime → str).
        result.result_schedule = schedule.model_dump(mode="json")
        raw_recipes = final_state.get("validated_recipes", [])
        result.result_recipes = [
            ValidatedRecipe.model_validate(r).model_dump(mode="json") for r in raw_recipes
        ]
    else:
        result.status = SessionStatus.FAILED

    if has_errors:
        error_messages = [f"{e.get('node_name', '?')}: {e.get('message', '?')}" for e in errors]
        result.error_summary = "; ".join(error_messages)

    # Persist accumulated LLM token usage for observability
    token_usage_records = final_state.get("token_usage", [])
    if token_usage_records:
        total_input = sum(r.get("input_tokens", 0) for r in token_usage_records)
        total_output = sum(r.get("output_tokens", 0) for r in token_usage_records)
        result.token_usage = {
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "per_node": token_usage_records,
        }

    result.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(result)
    await db.commit()


async def status_projection(
    session_id: uuid.UUID,
    graph,  # compiled LangGraph graph — avoid circular import with type hint
) -> SessionStatus:
    """
    Derives in-progress SessionStatus from the LangGraph checkpoint state.
    Returns GENERATING as the safe default if checkpoint is empty/unavailable.
    """
    config = {"configurable": {"thread_id": str(session_id)}}

    try:
        state_snapshot = await graph.aget_state(config)
        state = state_snapshot.values if state_snapshot else {}
    except Exception:
        return SessionStatus.GENERATING

    if not state:
        return SessionStatus.GENERATING

    # Derive status from the most advanced populated field
    if state.get("merged_dag") or state.get("recipe_dags"):
        return SessionStatus.SCHEDULING
    if state.get("validated_recipes"):
        return SessionStatus.SCHEDULING
    if state.get("enriched_recipes"):
        return SessionStatus.VALIDATING
    if state.get("raw_recipes"):
        return SessionStatus.ENRICHING

    return SessionStatus.GENERATING
