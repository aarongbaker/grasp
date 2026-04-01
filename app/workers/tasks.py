"""
app/workers/tasks.py
Celery tasks. Wraps graph.ainvoke() for pipeline execution.
finalise_session() is called explicitly after ainvoke() — same as in tests.

The LangGraph graph and checkpointer cannot be shared across processes.
Each Celery worker creates its own checkpointer connection and graph instance.
This is safe — LangGraph's PostgresSaver is stateless between invocations;
all state lives in Postgres, not in memory.
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
    """
    asyncio.run(_run_pipeline_async(session_id, user_id))


async def _run_pipeline_async(session_id: str, user_id: str):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

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

            # Load equipment for this user (one-to-many)
            from sqlmodel import select

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

            config = {"configurable": {"thread_id": session_id}}

            try:
                final_state = await graph.ainvoke(initial_state, config=config)
            except Exception as exc:
                # Unhandled exception — write FAILED status
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

            await finalise_session(uuid.UUID(session_id), final_state, db)

        await engine.dispose()


@celery_app.task(name="grasp.delete_cookbook_vectors")
def delete_cookbook_vectors(book_id: str, vector_ids: list[str]):
    """Best-effort Pinecone cleanup for a deleted cookbook. Runs out-of-band from the API request."""
    from pinecone import Pinecone

    if not settings.pinecone_api_key or not vector_ids:
        return

    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index_name)

    batch_size = 100
    for i in range(0, len(vector_ids), batch_size):
        index.delete(ids=vector_ids[i : i + batch_size])


@celery_app.task(name="grasp.ingest_cookbook")
def ingest_cookbook(job_id: str, user_id: str, pdf_bytes_b64: str, filename: str):
    """Ingestion pipeline task. pdf_bytes_b64 is base64-encoded (JSON-safe)."""
    import base64

    pdf_bytes = base64.b64decode(pdf_bytes_b64)
    asyncio.run(_ingest_async(job_id, user_id, pdf_bytes, filename))


async def _ingest_async(job_id: str, user_id: str, pdf_bytes: bytes, filename: str):
    import uuid as uuid_lib
    from datetime import datetime, timezone

    # Import all related SQLModel tables so foreign keys resolve in the worker
    import app.models.ingestion  # noqa: F401
    import app.models.user  # noqa: F401

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.ingestion.classifier import classify_document
    from app.ingestion.embedder import embed_and_upsert_chunks
    from app.ingestion.rasteriser import rasterise_and_ocr_pdf
    from app.ingestion.state_machine import run_state_machine
    from app.models.enums import IngestionStatus
    from app.models.ingestion import BookRecord, IngestionJob

    engine = create_async_engine(settings.database_url)
    SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        job = await db.get(IngestionJob, uuid_lib.UUID(job_id))
        if not job:
            return

        def _phase_status(phase: str, **extra):
            return {
                "title": filename,
                "status": job.status.value if hasattr(job.status, "value") else str(job.status),
                "phase": phase,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                **extra,
            }

        async def _commit_job_phase(phase: str, **extra):
            job.book_statuses = [_phase_status(phase, **extra)]
            db.add(job)
            await db.commit()

        async def _ocr_progress(page_done: int, pages_total: int):
            if page_done == 1 or page_done == pages_total or page_done % 25 == 0:
                print(f"[ingest:{job_id}] OCR progress {page_done}/{pages_total} pages for {filename}")
            await _commit_job_phase(
                "ocr",
                book_id=str(book.book_id) if "book" in locals() else None,
                pages_done=page_done,
                pages_total=pages_total,
            )

        job.status = IngestionStatus.PROCESSING
        await _commit_job_phase("queued")

        try:
            book = BookRecord(
                user_id=uuid_lib.UUID(user_id),
                title=filename,
            )
            db.add(book)
            await db.flush()
            await db.commit()
            await db.refresh(book)

            await _commit_job_phase("ocr", book_id=str(book.book_id), started_at=datetime.now(timezone.utc).isoformat())

            # Phase 2a: OCR
            pages = await rasterise_and_ocr_pdf(
                pdf_bytes,
                str(book.book_id),
                user_id,
                db,
                progress_callback=_ocr_progress,
            )
            await _commit_job_phase("classify", book_id=str(book.book_id), pages_total=len(pages))

            # Phase 2b: classify
            first_pages_text = " ".join(p["text"] for p in pages[:3])
            doc_type = await classify_document(first_pages_text)
            book.document_type = doc_type
            book.total_pages = len(pages)
            db.add(book)
            await db.commit()

            await _commit_job_phase(
                "chunk",
                book_id=str(book.book_id),
                pages_total=len(pages),
                document_type=getattr(doc_type, "value", doc_type),
            )

            # Phase 2c/2d: chunk
            chunks = run_state_machine(pages)

            await _commit_job_phase(
                "embed",
                book_id=str(book.book_id),
                pages_total=len(pages),
                chunks_total=len(chunks),
            )

            # Phase 2e: embed + upsert
            count = await embed_and_upsert_chunks(chunks, str(book.book_id), user_id, db)
            book.total_chunks = count
            db.add(book)

            job.status = IngestionStatus.COMPLETE
            job.completed = 1
            job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            job.book_statuses = [
                {
                    "title": filename,
                    "status": "complete",
                    "phase": "complete",
                    "book_id": str(book.book_id),
                    "pages_total": len(pages),
                    "chunks_total": len(chunks),
                    "embedded_chunks": count,
                    "completed_at": job.completed_at.isoformat(),
                }
            ]

        except Exception as e:
            await db.rollback()
            job = await db.get(IngestionJob, uuid_lib.UUID(job_id))
            if not job:
                return
            job.status = IngestionStatus.FAILED
            job.failed = 1
            job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            job.book_statuses = [{"title": filename, "status": "failed", "phase": "failed", "error": str(e)}]

        db.add(job)
        await db.commit()

    await engine.dispose()
