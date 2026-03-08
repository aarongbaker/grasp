"""
workers/tasks.py
Celery tasks. Wraps graph.ainvoke() for pipeline execution.
finalise_session() is called explicitly after ainvoke() — same as in tests.

The LangGraph graph and checkpointer cannot be shared across processes.
Each Celery worker creates its own checkpointer connection and graph instance.
This is safe — LangGraph's PostgresSaver is stateless between invocations;
all state lives in Postgres, not in memory.
"""

import asyncio
import uuid
from workers.celery_app import celery_app
from core.settings import get_settings

settings = get_settings()


@celery_app.task(name="grasp.run_pipeline")
def run_grasp_pipeline(session_id: str, user_id: str):
    """
    Main pipeline task. Creates its own event loop, checkpointer, and graph.
    Calls finalise_session() after ainvoke() regardless of outcome.
    """
    asyncio.run(_run_pipeline_async(session_id, user_id))


async def _run_pipeline_async(session_id: str, user_id: str):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlmodel import SQLModel
    from graph.graph import build_grasp_graph
    from core.status import finalise_session
    from models.session import Session
    from models.user import UserProfile, KitchenConfig
    from models.pipeline import DinnerConcept
    from models.enums import SessionStatus

    # Build per-worker graph + checkpointer
    async with AsyncPostgresSaver.from_conn_string(
        settings.langgraph_checkpoint_url
    ) as checkpointer:
        graph = build_grasp_graph(checkpointer)

        engine = create_async_engine(settings.database_url)
        SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        async with SessionLocal() as db:
            session = await db.get(Session, uuid.UUID(session_id))
            if not session:
                return

            user = await db.get(UserProfile, uuid.UUID(user_id))
            if not user:
                return

            kitchen = await db.get(KitchenConfig, user.kitchen_config_id)

            concept = DinnerConcept.model_validate(session.concept_json)

            initial_state = {
                "concept": concept.model_dump(),
                "kitchen_config": kitchen.model_dump() if kitchen else {},
                "raw_recipes": [],
                "enriched_recipes": [],
                "validated_recipes": [],
                "recipe_dags": [],
                "merged_dag": None,
                "schedule": None,
                "errors": [],
                "test_mode": None,
            }

            config = {"configurable": {"thread_id": session_id}}

            try:
                final_state = await graph.ainvoke(initial_state, config=config)
            except Exception as exc:
                # Unhandled exception — write FAILED status
                final_state = {
                    **initial_state,
                    "errors": [{
                        "node_name": "celery_task",
                        "error_type": "unknown",
                        "recoverable": False,
                        "message": str(exc),
                        "metadata": {"exception_type": type(exc).__name__},
                    }],
                }

            await finalise_session(uuid.UUID(session_id), final_state, db)

        await engine.dispose()


@celery_app.task(name="grasp.ingest_cookbook")
def ingest_cookbook(job_id: str, user_id: str, pdf_bytes_b64: str, filename: str):
    """Ingestion pipeline task. pdf_bytes_b64 is base64-encoded (JSON-safe)."""
    import base64
    pdf_bytes = base64.b64decode(pdf_bytes_b64)
    asyncio.run(_ingest_async(job_id, user_id, pdf_bytes, filename))


async def _ingest_async(job_id: str, user_id: str, pdf_bytes: bytes, filename: str):
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from models.ingestion import IngestionJob, BookRecord
    from models.enums import IngestionStatus
    from ingestion.rasteriser import rasterise_and_ocr_pdf
    from ingestion.classifier import classify_document
    from ingestion.state_machine import run_state_machine
    from ingestion.embedder import embed_and_upsert_chunks
    import uuid as uuid_lib
    from datetime import datetime, timezone

    engine = create_async_engine(settings.database_url)
    SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        job = await db.get(IngestionJob, uuid_lib.UUID(job_id))
        if not job:
            return

        job.status = IngestionStatus.PROCESSING
        db.add(job)
        await db.commit()

        try:
            book = BookRecord(
                user_id=uuid_lib.UUID(user_id),
                title=filename,
            )
            db.add(book)
            await db.commit()
            await db.refresh(book)

            # Phase 2a: OCR
            pages = await rasterise_and_ocr_pdf(pdf_bytes, str(book.book_id), user_id, db)

            # Phase 2b: classify
            first_pages_text = " ".join(p["text"] for p in pages[:3])
            doc_type = await classify_document(first_pages_text)
            book.document_type = doc_type
            book.total_pages = len(pages)
            db.add(book)
            await db.commit()

            # Phase 2c/2d: chunk
            chunks = run_state_machine(pages)

            # Phase 2e: embed + upsert
            count = await embed_and_upsert_chunks(chunks, str(book.book_id), user_id, db)
            book.total_chunks = count
            db.add(book)

            job.status = IngestionStatus.COMPLETE
            job.completed = 1
            job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        except Exception as e:
            job.status = IngestionStatus.FAILED
            job.failed = 1
            job.book_statuses = [{"title": filename, "status": "failed", "error": str(e)}]

        db.add(job)
        await db.commit()

    await engine.dispose()
