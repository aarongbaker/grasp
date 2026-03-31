from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import fitz
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.ingestion.state_machine import run_state_machine
from app.models.enums import ChunkType
from app.models.ingestion import BookRecord, CookbookChunk
from app.models.user import UserProfile

USER_EMAIL = "southern-verification@example.com"
USER_NAME = "Southern Verification User"
PDF_CANDIDATES = (
    Path("/Users/aaronbaker/Desktop/cookbooks/southerncookbook00lustrich.pdf"),
    Path("fixtures/southerncookbook00lustrich.pdf"),
    Path("tests/fixtures/southerncookbook00lustrich.pdf"),
)
OUTPUT_PATH = Path(".gsd/milestones/M012/slices/S03/southern-cookbook-verification.json")


@dataclass(frozen=True)
class ChunkSeed:
    page_number: int
    text: str
    chapter: str


def resolve_pdf_path() -> Path | None:
    for candidate in PDF_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _chapter_for_page(page_number: int) -> str:
    if page_number <= 7:
        return "Front Matter"
    if page_number <= 22:
        return "Fish and Shell Fish"
    if page_number <= 48:
        return "Breads and Cakes"
    return "Southern Cookbook"


def _normalise_whitespace(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def extract_recipe_chunks(pdf_path: Path) -> list[ChunkSeed]:
    doc = fitz.open(str(pdf_path))
    try:
        pages = [
            {"page_number": index + 1, "text": doc[index].get_text("text") or ""}
            for index in range(len(doc))
        ]
    finally:
        doc.close()

    chunks = run_state_machine(pages)
    recipe_chunks = [chunk for chunk in chunks if chunk["chunk_type"] == "recipe"]
    return [
        ChunkSeed(
            page_number=chunk["page_number"],
            text=_normalise_whitespace(chunk["text"]),
            chapter=_chapter_for_page(chunk["page_number"]),
        )
        for chunk in recipe_chunks
    ]


def build_fallback_chunks() -> list[ChunkSeed]:
    return [
        ChunkSeed(
            page_number=19,
            chapter="Fish and Shell Fish",
            text=(
                "Bouillabaisse\n"
                "2 pounds fish\n"
                "1 onion, sliced\n"
                "1 clove garlic\n"
                "Simmer gently until the fish flakes and place on buttered slices of toast."
            ),
        ),
        ChunkSeed(
            page_number=20,
            chapter="Fish and Shell Fish",
            text=(
                "Crab Croquettes\n"
                "2 cups crab meat\n"
                "1 teaspoon onion juice\n"
                "salt and pepper\n"
                "Fry in deep hot fat until golden brown."
            ),
        ),
    ]


async def ensure_user(session) -> UserProfile:
    result = await session.exec(select(UserProfile).where(UserProfile.email == USER_EMAIL))
    user = result.first()
    if user is not None:
        return user

    user = UserProfile(
        name=USER_NAME,
        email=USER_EMAIL,
        rag_owner_key=UserProfile.build_rag_owner_key(USER_EMAIL),
    )
    session.add(user)
    await session.flush()
    return user


async def ensure_book(session, user: UserProfile, title: str, author: str, total_pages: int, total_chunks: int) -> BookRecord:
    result = await session.exec(
        select(BookRecord).where(BookRecord.user_id == user.user_id).where(BookRecord.title == title)
    )
    book = result.first()
    if book is None:
        book = BookRecord(
            user_id=user.user_id,
            title=title,
            author=author,
            total_pages=total_pages,
            total_chunks=total_chunks,
        )
        session.add(book)
        await session.flush()
        return book

    book.author = author
    book.total_pages = total_pages
    book.total_chunks = total_chunks
    session.add(book)
    await session.flush()
    return book


async def sync_recipe_chunks(session, book: BookRecord, user: UserProfile, chunks: Iterable[ChunkSeed]) -> int:
    result = await session.exec(select(CookbookChunk).where(CookbookChunk.book_id == book.book_id))
    existing_chunks = result.all()
    existing_by_key = {(chunk.page_number, chunk.text): chunk for chunk in existing_chunks}

    added = 0
    for chunk_seed in chunks:
        key = (chunk_seed.page_number, chunk_seed.text)
        chunk = existing_by_key.get(key)
        if chunk is None:
            session.add(
                CookbookChunk(
                    book_id=book.book_id,
                    user_id=user.user_id,
                    text=chunk_seed.text,
                    chunk_type=ChunkType.RECIPE,
                    chapter=chunk_seed.chapter,
                    page_number=chunk_seed.page_number,
                    token_count=len(chunk_seed.text.split()),
                    pinecone_upserted=False,
                )
            )
            added += 1
            continue

        chunk.chapter = chunk_seed.chapter
        chunk.token_count = len(chunk_seed.text.split())
        chunk.chunk_type = ChunkType.RECIPE
        session.add(chunk)

    return added


def write_verification_artifact(payload: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


async def main() -> None:
    pdf_path = resolve_pdf_path()
    used_fallback = pdf_path is None
    seeded_chunks = build_fallback_chunks() if used_fallback else extract_recipe_chunks(pdf_path)

    async with SessionLocal() as session:
        user = await ensure_user(session)
        book = await ensure_book(
            session,
            user,
            title="The Southern Cook Book",
            author="Mrs. S. R. Dull",
            total_pages=max((chunk.page_number for chunk in seeded_chunks), default=0),
            total_chunks=len(seeded_chunks),
        )
        added_chunks = await sync_recipe_chunks(session, book, user, seeded_chunks)
        await session.commit()

    preview_titles = []
    for chunk in seeded_chunks[:5]:
        first_line = next((line.strip() for line in chunk.text.splitlines() if line.strip()), "")
        preview_titles.append({"page": chunk.page_number, "title": first_line})

    artifact = {
        "user_email": USER_EMAIL,
        "book_title": "The Southern Cook Book",
        "pdf_path": str(pdf_path) if pdf_path is not None else None,
        "used_fallback_chunks": used_fallback,
        "seeded_recipe_chunk_count": len(seeded_chunks),
        "new_chunks_added": added_chunks,
        "preview_titles": preview_titles,
        "browser_flow": [
            "Start the API and frontend with the repo-root local app commands.",
            "Sign in or impersonate the seeded user email in the local app's existing dev auth path.",
            "Open New Session and choose 'Schedule exact uploaded recipes'.",
            "Browse 'The Southern Cook Book' and verify early rows are recipe titles rather than page-based placeholders or catalog fragments.",
        ],
    }
    write_verification_artifact(artifact)

    print(f"verification_artifact={OUTPUT_PATH}")
    print(f"seeded_recipe_chunk_count={len(seeded_chunks)}")
    print(f"used_fallback_chunks={used_fallback}")
    if pdf_path is not None:
        print(f"source_pdf={pdf_path}")


if __name__ == "__main__":
    asyncio.run(main())
