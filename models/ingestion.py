"""
models/ingestion.py
Ingestion domain — SQLModel → Postgres + Pinecone.

BookRecord, PageCache, CookbookChunk, IngestionJob.

PageCache is a production requirement, not an optimisation. Raw Vision OCR
output per page is persisted to Postgres BEFORE any processing. This enables:
  1. Pipeline reprocessing without re-running OCR as state machine improves
  2. Page-by-page retry on partial failures
  3. Provenance auditability — any RAG result traceable to exact source page
  4. Future admin tooling for chef review/correction

CookbookChunk.user_id is denormalised (it's also reachable via book_id → user_id)
specifically for Pinecone metadata. Pinecone filters can't do joins — the user_id
must be in the chunk metadata envelope for per-chef RAG isolation to work.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON
from models.enums import DocumentType, ChunkType, IngestionStatus


class BookRecord(SQLModel, table=True):
    __tablename__ = "book_records"

    book_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    title: str
    author: str = ""
    # None until classifier runs — chef can always override via UI
    document_type: Optional[DocumentType] = None
    total_pages: int = 0
    total_chunks: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PageCache(SQLModel, table=True):
    """
    Raw Vision OCR output, one row per page. Canonical ground truth.
    page_hash is SHA256 of the source PDF page bytes — enables change detection
    if the same PDF is re-ingested after editing.
    """
    __tablename__ = "page_cache"

    page_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    book_id: uuid.UUID = Field(foreign_key="book_records.book_id", index=True)
    page_number: int
    page_text: str
    page_hash: str                         # SHA256 of source PDF page bytes
    vision_confidence: float = 0.0        # Apple Vision's reported confidence
    resolution_dpi: int = Field(default=300)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CookbookChunk(SQLModel, table=True):
    __tablename__ = "cookbook_chunks"

    chunk_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    book_id: uuid.UUID = Field(foreign_key="book_records.book_id", index=True)
    # Denormalised for Pinecone metadata — needed for per-chef filter without joins
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    text: str
    chunk_type: ChunkType
    chapter: str = ""
    page_number: int = 0
    token_count: int = 0
    pinecone_upserted: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_pinecone_metadata(self) -> dict:
        """Metadata envelope for Pinecone upsert. user_id enables per-chef isolation."""
        return {
            "user_id": str(self.user_id),
            "book_id": str(self.book_id),
            "chunk_id": str(self.chunk_id),
            "chunk_type": self.chunk_type.value,
            "chapter": self.chapter,
            "page_number": self.page_number,
        }


class IngestionJob(SQLModel, table=True):
    """
    Tracks a multi-book upload. book_statuses is a JSON array:
    [{book_id, title, status, error}] — one entry per book in the batch.
    """
    __tablename__ = "ingestion_jobs"

    job_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    status: IngestionStatus = Field(default=IngestionStatus.PENDING)
    book_count: int = 0
    completed: int = 0
    failed: int = 0
    book_statuses: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
