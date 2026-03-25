"""api/routes/ingest.py — PDF upload and ingestion job polling."""

import base64
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.models.enums import IngestionStatus
from app.models.ingestion import BookRecord, IngestionJob

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/ingest")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


@router.post("", status_code=202)
@limiter.limit("10/hour")
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    db: DBSession = ...,
    current_user: CurrentUser = ...,
):
    """Upload a PDF. Returns job_id immediately. Background processing via Celery."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")

    content = await file.read()

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

    job = IngestionJob(
        user_id=current_user.user_id,
        status=IngestionStatus.PENDING,
        book_count=1,
        book_statuses=[{"title": file.filename, "status": "pending"}],
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue ingestion task — base64-encode PDF bytes for JSON serialiser.
    # TODO: V2 should store to object storage and pass a reference instead.
    from app.workers.tasks import ingest_cookbook

    content_b64 = base64.b64encode(content).decode("ascii")
    ingest_cookbook.delay(str(job.job_id), str(current_user.user_id), content_b64, file.filename)

    return {"job_id": str(job.job_id)}


@router.get("/cookbooks")
async def list_cookbooks(db: DBSession, current_user: CurrentUser):
    """Returns all ingested cookbooks for the current user, newest first."""
    statement = (
        select(BookRecord)
        .where(BookRecord.user_id == current_user.user_id)
        .order_by(BookRecord.created_at.desc())
    )
    results = await db.exec(statement)
    books = results.all()
    return [
        {
            "book_id": str(b.book_id),
            "title": b.title,
            "author": b.author,
            "document_type": b.document_type.value if b.document_type else None,
            "total_pages": b.total_pages,
            "total_chunks": b.total_chunks,
            "created_at": b.created_at.isoformat(),
        }
        for b in books
    ]


@router.get("/{job_id}")
async def get_ingestion_status(job_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    job = await db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    if job.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return job
