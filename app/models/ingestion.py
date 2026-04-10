"""
models/ingestion.py

INTERNAL INFRASTRUCTURE — Ingestion domain — SQLModel → Postgres + Pinecone.

After M015 pivot (cookbook de-scope), these models support team/admin curated
cookbook uploads only, not user-facing cookbook upload flows.

BookRecord, PageCache, CookbookChunk, IngestionJob.

PageCache is a production requirement, not an optimisation. Raw Vision OCR
output per page is persisted to Postgres BEFORE any processing. This enables:
  1. Pipeline reprocessing without re-running OCR as state machine improves
  2. Page-by-page retry on partial failures
  3. Provenance auditability — any RAG result traceable to exact source page
  4. Future admin tooling for review/correction

CookbookChunk.user_id is denormalised (it's also reachable via book_id → user_id)
specifically for Pinecone metadata. Pinecone filters can't do joins — the user_id
must be in the chunk metadata envelope for per-user RAG isolation to work.

See: .gsd/milestones/M015/slices/S03/S03-CONTEXT.md for enrichment contract.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, String
from sqlmodel import Column, Field, SQLModel

from app.models.enums import ChunkType, DocumentType, IngestionStatus


class BookRecord(SQLModel, table=True):
    """One row per uploaded PDF. Parent of PageCache and CookbookChunk rows."""

    __tablename__ = "book_records"

    book_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # user_id scopes the book to one chef. Indexed for fast per-user book listing.
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)

    title: str   # defaults to filename at upload; chef can rename later
    author: str = ""

    # Set by the classifier (ingestion/classifier.py) after OCR completes.
    # None until then — chef can also override via admin UI before finalization.
    # Stored as String (not native enum) for portability across Postgres versions.
    document_type: Optional[DocumentType] = Field(default=None, sa_column=Column(String, nullable=True))

    total_pages: int = 0    # set after OCR; used for progress display
    total_chunks: int = 0   # set after embedding; used for RAG coverage stats

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class PageCache(SQLModel, table=True):
    """
    Raw Vision OCR output, one row per page. Canonical ground truth.
    page_hash is SHA256 of the source PDF page bytes — enables change detection
    if the same PDF is re-ingested after editing.

    Written BEFORE any downstream processing (chunking, embedding).
    If the state machine or embedder crashes, OCR output is safe here.
    Re-running OCR on a 300-page cookbook can take minutes — this avoids it.
    """

    __tablename__ = "page_cache"

    page_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # Foreign key to the parent book. Indexed for fast "fetch all pages for book" queries
    # used by the state machine when reprocessing an existing BookRecord.
    book_id: uuid.UUID = Field(foreign_key="book_records.book_id", index=True)

    page_number: int    # 1-indexed (OCR output order, not PDF internal numbering)
    page_text: str      # raw OCR text — may include noise, headers, page numbers

    # SHA256 of source PDF page content. Stable across re-ingestions of the same PDF.
    # Used to detect unchanged pages and skip redundant OCR in future reprocessing.
    page_hash: str

    # Apple Vision provides real confidence per observation. Tesseract and pymupdf
    # use synthetic values (0.85 and 0.7 respectively) — treat as relative quality
    # indicators, not absolute probabilities.
    vision_confidence: float = 0.0

    # Always 300 DPI in V1 — stored for future quality branching (e.g. 600 DPI for
    # dense technical diagrams). No quality branching in current codebase.
    resolution_dpi: int = Field(default=300)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class CookbookChunk(SQLModel, table=True):
    """One semantically coherent text unit, ready for embedding and RAG retrieval.

    Produced by the state machine (state_machine.py) from PageCache rows.
    Embedding is done by embedder.py which upserts to Pinecone and sets pinecone_upserted=True.

    user_id is denormalized here (reachable via book_id → BookRecord.user_id) because
    Pinecone metadata filters operate on flat key-value pairs with no join capability.
    The per-user RAG isolation contract requires user_id in every chunk's Pinecone metadata.
    """

    __tablename__ = "cookbook_chunks"

    chunk_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    book_id: uuid.UUID = Field(foreign_key="book_records.book_id", index=True)

    # Denormalised for Pinecone metadata — needed for per-chef filter without joins.
    # Also used for DB-level user-scoped chunk queries (e.g. admin review tooling).
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)

    text: str       # the raw chunk text upserted to Pinecone as the searchable body
    chunk_type: ChunkType = Field(sa_column=Column(String, nullable=False))
    chapter: str = ""       # book chapter heading, for provenance display
    page_number: int = 0    # source page, for provenance tracing

    # Approximate word count — used for embedder batch size planning and
    # to monitor chunk size distribution in admin tooling.
    token_count: int = 0

    # Set to True after successful Pinecone upsert. False means the chunk exists
    # in Postgres but is not yet searchable via RAG — useful for retry logic.
    pinecone_upserted: bool = Field(default=False)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    def to_pinecone_metadata(self) -> dict:
        """Metadata envelope for Pinecone upsert. Stable owner key is attached by the embedder.

        This dict is stored alongside each Pinecone vector. The enricher's
        _retrieve_rag_context() reads these fields to filter by user_id/rag_owner_key
        and to extract the 'text' field as advisory RAG context for the LLM.
        """
        return {
            "user_id": str(self.user_id),
            "book_id": str(self.book_id),
            "chunk_id": str(self.chunk_id),
            "chunk_type": self.chunk_type.value,
            "chapter": self.chapter,
            "page_number": self.page_number,
            "text": self.text,
            # rag_owner_key is NOT included here — the embedder adds it separately
            # so the key is resolved from the live UserProfile at embed time, not
            # captured in the Pydantic model (where it would be stale after migration).
        }


class IngestionJob(SQLModel, table=True):
    """
    Tracks a multi-book upload. book_statuses is a JSON array:
    [{book_id, title, status, phase, error}] — one entry per book in the batch.

    Status transitions: PENDING → PROCESSING → COMPLETE / FAILED
    The Celery task (tasks.py ingest_cookbook) updates status and book_statuses
    at each ingestion phase (queued → ocr → classify → chunk → embed → complete).
    """

    __tablename__ = "ingestion_jobs"

    job_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # User who initiated the upload. Indexed for fast per-user job listing.
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)

    # Overall job status. PROCESSING = Celery task is running.
    # COMPLETE/FAILED = terminal. Stored as String for portability.
    status: IngestionStatus = Field(default=IngestionStatus.PENDING, sa_column=Column(String, nullable=False))

    book_count: int = 0   # total books in this upload batch
    completed: int = 0    # books that reached COMPLETE
    failed: int = 0       # books that reached FAILED

    # Celery task ID — used to check task health and to cancel in-progress jobs
    celery_task_id: Optional[str] = None

    # Per-book status detail array. Each entry has: title, status, phase,
    # book_id (once created), pages_done/total, chunks_total, error (on failure).
    # Updated by the Celery task at each phase transition for live progress display.
    book_statuses: list[dict] = Field(default_factory=list, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    completed_at: Optional[datetime] = None  # set when status reaches COMPLETE or FAILED
