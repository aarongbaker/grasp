"""api/routes/ingest.py — PDF upload and ingestion job polling."""

import base64
import re
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from sqlalchemy import delete
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.models.enums import ChunkType, IngestionStatus
from app.models.ingestion import BookRecord, IngestionJob

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/ingest")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_NON_RECIPE_DETECTED_TITLES = (
    "index",
    "introduction",
    "foreword",
    "preface",
    "contents",
)


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:160]
    return ""


def _looks_like_detected_recipe_noise(chunk_text: str, recipe_name: str) -> bool:
    lowered_name = recipe_name.strip().lower()
    if any(lowered_name.startswith(prefix) for prefix in _NON_RECIPE_DETECTED_TITLES):
        return True

    lowered_text = chunk_text.lower()
    if "index page" in lowered_text or lowered_text.startswith("index "):
        return True
    if lowered_text.startswith("introduction ") and "cook book" in lowered_text:
        return True
    if lowered_text.count("the southern cook book") >= 2:
        return True
    if len(re.findall(r"\bpage\b", lowered_text)) >= 3 and len(re.findall(r"\b\d{1,3}\b", lowered_text)) >= 8:
        return True
    return False


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

    def _document_type_value(value):
        if value is None:
            return None
        return getattr(value, "value", value)

    return [
        {
            "book_id": str(b.book_id),
            "title": b.title,
            "author": b.author,
            "document_type": _document_type_value(b.document_type),
            "total_pages": b.total_pages,
            "total_chunks": b.total_chunks,
            "created_at": b.created_at.isoformat(),
        }
        for b in books
    ]


@router.get("/detected-recipes")
async def list_detected_recipes(db: DBSession, current_user: CurrentUser):
    """Returns recipe-like cookbook chunks for the current user, grouped by source book metadata."""
    from app.models.ingestion import CookbookChunk

    statement = (
        select(CookbookChunk, BookRecord)
        .join(BookRecord, CookbookChunk.book_id == BookRecord.book_id)
        .where(CookbookChunk.user_id == current_user.user_id)
        .where(CookbookChunk.chunk_type == ChunkType.RECIPE)
        .order_by(BookRecord.created_at.desc(), CookbookChunk.page_number.asc(), CookbookChunk.created_at.asc())
    )
    results = await db.exec(statement)
    rows = results.all()

    def _detect_recipe_name(chunk: CookbookChunk) -> str:
        first_line = next((line.strip() for line in chunk.text.splitlines() if line.strip()), "")
        return first_line[:160] if first_line else f"Recipe on page {chunk.page_number or '—'}"

    return [
        {
            "chunk_id": str(chunk.chunk_id),
            "book_id": str(book.book_id),
            "book_title": book.title,
            "recipe_name": recipe_name,
            "chapter": chunk.chapter,
            "page_number": chunk.page_number,
            "text": chunk.text,
        }
        for chunk, book in rows
        if not _looks_like_detected_recipe_noise(chunk.text, (recipe_name := _detect_recipe_name(chunk)))
    ]


@router.delete("/cookbooks/{book_id}", status_code=204)
async def delete_cookbook(book_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """Delete a cookbook and its associated chunk/page metadata for the current user."""
    from app.models.ingestion import CookbookChunk, PageCache

    book = await db.get(BookRecord, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Cookbook not found")
    if book.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    chunk_results = await db.exec(select(CookbookChunk).where(CookbookChunk.book_id == book_id))
    chunks = chunk_results.all()
    vector_ids = [str(chunk.chunk_id) for chunk in chunks]

    await db.exec(delete(CookbookChunk).where(CookbookChunk.book_id == book_id))
    await db.exec(delete(PageCache).where(PageCache.book_id == book_id))
    await db.exec(delete(BookRecord).where(BookRecord.book_id == book_id))
    await db.commit()

    if vector_ids:
        try:
            from app.workers.tasks import delete_cookbook_vectors

            delete_cookbook_vectors.delay(str(book_id), vector_ids)
        except Exception:
            # Relational delete already succeeded. Vector cleanup is best-effort.
            pass


@router.get("/{job_id}")
async def get_ingestion_status(job_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    job = await db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    if job.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return job
