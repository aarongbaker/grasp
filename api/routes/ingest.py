"""api/routes/ingest.py — PDF upload and ingestion job polling."""
import uuid
import base64
from fastapi import APIRouter, UploadFile, File, HTTPException
from sqlmodel import select
from core.deps import DBSession, CurrentUser
from models.ingestion import IngestionJob, BookRecord
from models.enums import IngestionStatus

router = APIRouter(prefix="/ingest")


@router.post("", status_code=202)
async def upload_pdf(
    file: UploadFile = File(...),
    db: DBSession = ...,
    current_user: CurrentUser = ...,
):
    """Upload a PDF. Returns job_id immediately. Background processing via Celery."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")

    content = await file.read()

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
    from workers.tasks import ingest_cookbook
    content_b64 = base64.b64encode(content).decode("ascii")
    ingest_cookbook.delay(str(job.job_id), str(current_user.user_id), content_b64, file.filename)

    return {"job_id": str(job.job_id)}


@router.get("/{job_id}")
async def get_ingestion_status(job_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    job = await db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    if job.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return job
