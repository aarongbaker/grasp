"""
app/workers/tasks.py
Celery tasks. Wraps graph.ainvoke() for pipeline execution.
finalise_session() is called explicitly after ainvoke() — same as in tests.

Why asyncio.run() inside a Celery task?
  Celery workers are synchronous by default. LangGraph's async nodes (generator,
  enricher, renderer) use await internally. asyncio.run() creates a fresh event
  loop for each task invocation, runs the async pipeline to completion, then
  tears it down. This is the correct pattern for sync→async bridging in Celery.
  Do NOT use asyncio.get_event_loop().run_until_complete() — it's deprecated
  and fails if there's already a running loop (e.g. in test environments).

Why build a new graph + checkpointer per task?
  The LangGraph graph and checkpointer cannot be shared across processes.
  Each Celery worker creates its own AsyncPostgresSaver connection and graph
  instance. This is safe — PostgresSaver is stateless between invocations;
  all state lives in Postgres, not in the Python process. Two workers processing
  different sessions can safely use separate PostgresSaver instances pointing
  at the same Postgres database.

Why create a new SQLAlchemy engine per task?
  The main app's engine (db/session.py) lives in the API server process.
  Celery workers are separate processes — they can't share the API server's
  connection pool (different process, different memory). Each task creates
  its own engine and SessionLocal, uses them for the task, then disposes
  the engine to return connections to the OS.
"""

import asyncio
import uuid

from pydantic import ValidationError

from app.core.settings import get_settings
from app.workers.celery_app import celery_app

settings = get_settings()


@celery_app.task(name="grasp.run_pipeline")
def run_grasp_pipeline(session_id: str, user_id: str):
    """
    Main pipeline task. Creates its own event loop, checkpointer, and graph.
    Calls finalise_session() after ainvoke() regardless of outcome.

    The task name "grasp.run_pipeline" must match what POST /sessions/{id}/run
    uses to enqueue — run_grasp_pipeline.delay() resolves this via Celery's
    task registry.
    """
    asyncio.run(_run_pipeline_async(session_id, user_id))


async def _run_pipeline_async(session_id: str, user_id: str):
    """Async implementation of the pipeline task.

    Separated from run_grasp_pipeline() to allow clean async/await usage.
    asyncio.run() in the sync task wrapper creates the event loop and calls
    this coroutine.

    Error handling layers:
      1. ValidationError on concept_json: concept was valid at creation time
         but schema changed since. Write FAILED immediately — don't enter graph.
      2. Exception from graph.ainvoke(): unhandled graph error. Write FAILED
         with a synthetic error dict. finalise_session() always runs.
      3. No error: finalise_session() reads final_state and writes COMPLETE or FAILED
         depending on whether a schedule was produced.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlmodel import select

    from app.core.status import finalise_session
    from app.graph.graph import build_grasp_graph
    from app.models.errors import NodeError
    from app.models.enums import ErrorType
    from app.models.pipeline import build_session_initial_state
    from app.models.session import Session
    from app.models.user import Equipment, KitchenConfig, UserProfile

    async with AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_url) as checkpointer:
        await checkpointer.setup()
        graph = build_grasp_graph(checkpointer)

        # Create a fresh engine per task — Celery workers are separate processes
        # from the API server and cannot share its connection pool.
        engine = create_async_engine(settings.database_url)
        SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        async with SessionLocal() as db:
            # Load session and user — bail early if either was deleted between
            # enqueue and task execution (uncommon but possible).
            session = await db.get(Session, uuid.UUID(session_id))
            if not session:
                return

            user = await db.get(UserProfile, uuid.UUID(user_id))
            if not user:
                return

            # KitchenConfig is a one-to-one relationship loaded explicitly.
            # Async sessions don't support lazy loading — must use db.get().
            kitchen = await db.get(KitchenConfig, user.kitchen_config_id)

            # Equipment is one-to-many — load all rows for this user.
            equipment_result = await db.execute(select(Equipment).where(Equipment.user_id == uuid.UUID(user_id)))
            equipment_rows = equipment_result.scalars().all()

            try:
                _, initial_state = build_session_initial_state(
                    concept_payload=session.concept_json,
                    user_id=user_id,
                    rag_owner_key=user.rag_owner_key,
                    kitchen_config=kitchen.model_dump() if kitchen else {},
                    equipment=[e.model_dump() for e in equipment_rows],
                )
            except ValidationError as exc:
                # The stored concept_json failed Pydantic validation — schema mismatch.
                # Write a FAILED terminal state immediately without entering the graph.
                # This path is rare but important: a schema migration that changes
                # DinnerConcept fields could make old sessions unrunnable.
                validation_error = NodeError(
                    node_name="pipeline_startup",
                    error_type=ErrorType.VALIDATION_FAILURE,
                    recoverable=False,
                    message=f"Persisted session concept is invalid: {exc}",
                    metadata={"exception_type": type(exc).__name__},
                )
                await finalise_session(
                    uuid.UUID(session_id),
                    {
                        "concept": session.concept_json,
                        "kitchen_config": kitchen.model_dump() if kitchen else {},
                        "equipment": [e.model_dump() for e in equipment_rows],
                        "user_id": user_id,
                        "rag_owner_key": user.rag_owner_key,
                        "raw_recipes": [],
                        "enriched_recipes": [],
                        "validated_recipes": [],
                        "recipe_dags": [],
                        "merged_dag": None,
                        "schedule": None,
                        "errors": [validation_error.model_dump(mode="json")],
                    },
                    db,
                )
                await engine.dispose()
                return

            # thread_id = session_id: LangGraph uses thread_id to namespace all
            # checkpoint state for this pipeline run. Using session_id ensures
            # each session has its own isolated checkpoint history.
            config = {"configurable": {"thread_id": session_id}}

            try:
                final_state = await graph.ainvoke(initial_state, config=config)
            except Exception as exc:
                # Unhandled exception outside the graph's own error handling.
                # Build a minimal final_state so finalise_session() can write FAILED.
                # The graph's internal error handling (per-recipe isolation) catches
                # most failures — this outer catch is a last resort.
                final_state = {
                    **initial_state,
                    "errors": [
                        {
                            "node_name": "celery_task",
                            "error_type": "unknown",
                            "recoverable": False,
                            "message": str(exc),
                            "metadata": {"exception_type": type(exc).__name__},
                        }
                    ],
                }

            # finalise_session() always runs — even on pipeline failure.
            # It reads final_state["schedule"] to decide COMPLETE vs FAILED,
            # and writes completed_at, error_summary, and token_usage regardless.
            await finalise_session(uuid.UUID(session_id), final_state, db)

        await engine.dispose()
