"""
core/status.py
finalise_session() and status_projection() — the two functions that
manage Session.status as a read-optimised projection of the checkpoint.

finalise_session():
  Called ONCE when the pipeline ends (by Celery task wrapper in production,
  explicitly in tests). Reads terminal GRASPState from checkpoint. Writes
  terminal status, schedule_summary, total_duration_minutes, error_summary,
  completed_at to Session row.

  Why denormalise to the Session row at all? LangGraph checkpoints are
  optimised for graph resumption, not fast key-value reads. The Session table
  is a read-optimised projection — listing sessions in the UI only needs the
  DB row, not a checkpoint lookup per row.

  Cancellation guard: if the session was CANCELLED while the pipeline was
  running (race between cancel request and pipeline completion), finalise_session
  skips the write and rolls back. CANCELLED is a terminal state set by the
  cancel route, and we don't want pipeline completion to un-cancel it.

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

  The safe default (GENERATING) means the UI shows "generating" when the
  checkpoint is empty — which is correct for the brief window between
  /run enqueue and the first node writing to state.
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

    Uses SELECT ... FOR UPDATE to prevent concurrent writes (e.g. if the route
    handler and a stale Celery task both call finalise_session for the same session).
    populate_existing=True forces SQLAlchemy to refresh its in-session cache with
    the locked DB row — otherwise we'd see stale cached state.

    Result columns (result_schedule, result_recipes) are populated for COMPLETE
    sessions so that GET /sessions/{id}/results can serve them without a checkpoint
    lookup. This is the "fast path" in get_session_results().
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

    # Cancellation guard: if the cancel route wrote CANCELLED while the pipeline
    # was still running, respect the cancellation and don't overwrite it.
    # The pipeline's final_state is discarded in this case.
    if result.status == SessionStatus.CANCELLED:
        await db.rollback()
        return

    errors: list[dict] = final_state.get("errors", [])
    has_errors = len(errors) > 0

    schedule_dict = final_state.get("schedule")
    if schedule_dict:
        # Pipeline succeeded (may have partial errors from dropped recipes).
        # Denormalise summary and duration to Session for fast list-view reads.
        schedule = NaturalLanguageSchedule.model_validate(schedule_dict)
        result.schedule_summary = schedule.summary
        result.total_duration_minutes = schedule.total_duration_minutes
        result.status = SessionStatus.COMPLETE

        # Persist full results to avoid checkpoint lookups on GET /results.
        # model_dump(mode="json") ensures datetime and UUID fields are
        # serialised to strings — JSON columns need plain Python types.
        result.result_schedule = schedule.model_dump(mode="json")
        raw_recipes = final_state.get("validated_recipes", [])
        result.result_recipes = [
            ValidatedRecipe.model_validate(r).model_dump(mode="json") for r in raw_recipes
        ]
    else:
        # No schedule produced — pipeline failed (all recipes dropped or fatal error).
        result.status = SessionStatus.FAILED

    if has_errors:
        # Concatenate per-recipe error messages for the session list error summary.
        # Full error details are in the checkpoint; this is just a quick read.
        error_messages = [f"{e.get('node_name', '?')}: {e.get('message', '?')}" for e in errors]
        result.error_summary = "; ".join(error_messages)

    # Persist accumulated LLM token usage for observability and billing attribution.
    # per_node is the raw list; total_* are pre-summed for quick dashboard reads.
    token_usage_records = final_state.get("token_usage", [])
    if token_usage_records:
        total_input = sum(r.get("input_tokens", 0) for r in token_usage_records)
        total_output = sum(r.get("output_tokens", 0) for r in token_usage_records)
        result.token_usage = {
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "per_node": token_usage_records,
        }

    # UTC naive timestamp — the DB stores UTC without timezone info.
    # replace(tzinfo=None) strips the aware timezone before insert to match
    # the TIMESTAMP WITHOUT TIME ZONE column type.
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

    Uses graph.aget_state() to read the latest checkpoint without acquiring
    a write lock. This is a read-only operation — the checkpoint is not modified.

    The status hierarchy is based on which pipeline stage has completed:
    each stage writes a specific GRASPState field. Reading which fields are
    populated tells us how far the pipeline has gotten without tracking
    node names explicitly.
    """
    config = {"configurable": {"thread_id": str(session_id)}}

    try:
        state_snapshot = await graph.aget_state(config)
        state = state_snapshot.values if state_snapshot else {}
    except Exception:
        # Checkpoint unavailable (DB connection issue, graph not yet initialised).
        # Return GENERATING — the UI shows "in progress" which is true.
        return SessionStatus.GENERATING

    if not state:
        # Empty checkpoint — graph hasn't written anything yet.
        # This is normal for the brief window between task enqueue and first node.
        return SessionStatus.GENERATING

    # Status derivation: check fields from most-advanced to least-advanced.
    # merged_dag is set by dag_merger (last scheduling step).
    # recipe_dags is set by dag_builder (first scheduling step).
    # Both indicate SCHEDULING — the merger may still be running.
    if state.get("merged_dag") or state.get("recipe_dags"):
        return SessionStatus.SCHEDULING
    # validated_recipes is set by the validator. The next step is dag_builder,
    # but we show SCHEDULING here to avoid a confusing VALIDATING → SCHEDULING
    # transition that the user can't distinguish from SCHEDULING → SCHEDULING.
    if state.get("validated_recipes"):
        return SessionStatus.SCHEDULING
    if state.get("enriched_recipes"):
        return SessionStatus.VALIDATING
    if state.get("raw_recipes"):
        return SessionStatus.ENRICHING

    return SessionStatus.GENERATING
