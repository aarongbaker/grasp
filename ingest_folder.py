"""
ingest_folder.py
Bulk cookbook ingestion — bypasses Celery, calls pipeline functions directly.

Usage:
    .venv/bin/python ingest_folder.py ~/Desktop/cookbooks/
    .venv/bin/python ingest_folder.py ~/Desktop/cookbooks/ --user-id <uuid>

Requires:
    - docker compose up -d postgres
    - OPENAI_API_KEY in .env (embeddings)
    - PINECONE_API_KEY in .env (vector upsert)
"""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()


async def _ensure_dev_user(db) -> uuid.UUID:
    """Create or reuse a default dev user for local ingestion."""
    from sqlmodel import select
    from models.user import UserProfile, KitchenConfig

    DEV_EMAIL = "dev@grasp.local"
    result = await db.execute(select(UserProfile).where(UserProfile.email == DEV_EMAIL))
    user = result.scalars().first()
    if user:
        print(f"  Using existing dev user: {user.user_id}")
        return user.user_id

    kitchen = KitchenConfig()
    db.add(kitchen)
    await db.flush()

    user = UserProfile(
        name="Dev Chef",
        email=DEV_EMAIL,
        kitchen_config_id=kitchen.kitchen_config_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    print(f"  Created dev user: {user.user_id}")
    return user.user_id


async def _ingest_one_pdf(
    pdf_path: Path,
    user_id: uuid.UUID,
    db,
    index: int,
    total: int,
) -> dict:
    """Ingest a single PDF. Returns result dict, or None if already ingested."""
    from sqlmodel import select
    from ingestion.rasteriser import rasterise_and_ocr_pdf
    from ingestion.classifier import classify_document
    from ingestion.state_machine import run_state_machine
    from ingestion.embedder import embed_and_upsert_chunks
    from models.ingestion import BookRecord

    filename = pdf_path.name

    # Skip if already fully ingested for this user
    existing = await db.execute(
        select(BookRecord).where(
            BookRecord.title == filename,
            BookRecord.user_id == user_id,
        )
    )
    prev = existing.scalars().first()
    if prev and prev.total_pages > 0:
        print(f"  [{index}/{total}] {filename} — already ingested, skipping")
        return None
    if prev:
        # Incomplete previous run — remove stale record + orphaned children, then re-ingest
        from sqlalchemy import delete as sa_delete
        from models.ingestion import PageCache, CookbookChunk
        await db.execute(sa_delete(PageCache).where(PageCache.book_id == prev.book_id))
        await db.execute(sa_delete(CookbookChunk).where(CookbookChunk.book_id == prev.book_id))
        await db.delete(prev)
        await db.commit()

    book_id = str(uuid.uuid4())

    # Create BookRecord
    book = BookRecord(
        book_id=uuid.UUID(book_id),
        user_id=user_id,
        title=filename,
    )
    db.add(book)
    await db.flush()

    # Phase 2a: OCR — pass file path so pymupdf can memory-map instead of loading all bytes
    print(f"  [{index}/{total}] {filename} — rasterising...", end="", flush=True)
    pages = await rasterise_and_ocr_pdf(pdf_path, book_id, str(user_id), db)
    print(f" {len(pages)} pages", end="", flush=True)

    # Check OCR quality — warn if too many pages have empty text
    empty_pages = sum(1 for p in pages if not p["text"].strip())
    if empty_pages > len(pages) * 0.5:
        print(f" — WARNING: {empty_pages}/{len(pages)} pages have no OCR text (scanned PDF?)", end="", flush=True)

    # Phase 2b: Classify
    first_pages_text = " ".join(p["text"] for p in pages[:3])
    doc_type = await classify_document(first_pages_text)
    book.document_type = doc_type
    print(f" — {doc_type.value}", end="", flush=True)

    # Phase 2c: Chunk
    chunks = run_state_machine(pages)
    print(f" — {len(chunks)} chunks", end="", flush=True)

    # Phase 2e: Embed + upsert
    if chunks:
        count = await embed_and_upsert_chunks(chunks, book_id, str(user_id), db)
    else:
        count = 0

    # Update BookRecord
    book.total_pages = len(pages)
    book.total_chunks = count
    db.add(book)
    await db.commit()

    print(f" — done")
    return {"filename": filename, "pages": len(pages), "chunks": count, "type": doc_type.value}


async def main(folder: str, user_id_str: str | None):
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlmodel import SQLModel
    from core.settings import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)

    # Ensure tables exist
    import models.user       # noqa: F401
    import models.session    # noqa: F401
    import models.ingestion  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Collect PDFs
    folder_path = Path(folder).expanduser().resolve()
    if not folder_path.is_dir():
        print(f"Error: {folder_path} is not a directory")
        sys.exit(1)

    pdfs = sorted(folder_path.glob("*.pdf"))
    if not pdfs:
        print(f"No .pdf files found in {folder_path}")
        sys.exit(1)

    print(f"Found {len(pdfs)} PDFs in {folder_path}\n")

    async with SessionLocal() as db:
        # Resolve user ID
        if user_id_str:
            uid = uuid.UUID(user_id_str)
            print(f"  Using provided user ID: {uid}")
        else:
            uid = await _ensure_dev_user(db)

        print()

        # Ingest each PDF
        results = []
        failures = []
        for i, pdf_path in enumerate(pdfs, 1):
            try:
                result = await _ingest_one_pdf(pdf_path, uid, db, i, len(pdfs))
                if result is not None:
                    results.append(result)
            except Exception as exc:
                print(f" — FAILED: {exc}")
                failures.append({"filename": pdf_path.name, "error": str(exc)})
                await db.rollback()

    await engine.dispose()

    # Summary
    print(f"\n{'='*60}")
    print(f"INGESTION COMPLETE")
    print(f"{'='*60}")
    print(f"  Successes: {len(results)}")
    print(f"  Failures:  {len(failures)}")
    total_chunks = sum(r["chunks"] for r in results)
    total_pages = sum(r["pages"] for r in results)
    print(f"  Total pages:  {total_pages}")
    print(f"  Total chunks: {total_chunks}")
    print(f"  User ID: {uid}")

    if failures:
        print(f"\nFailed books:")
        for f in failures:
            print(f"  - {f['filename']}: {f['error']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk ingest cookbook PDFs")
    parser.add_argument("folder", help="Path to folder containing PDF files")
    parser.add_argument("--user-id", help="User UUID (creates dev user if omitted)")
    args = parser.parse_args()

    asyncio.run(main(args.folder, args.user_id))
