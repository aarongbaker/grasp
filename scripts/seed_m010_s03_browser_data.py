import asyncio
from pathlib import Path
import sys

import bcrypt
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.models.enums import ChunkType
from app.models.ingestion import BookRecord, CookbookChunk
from app.models.user import KitchenConfig, UserProfile

USER_EMAIL = "m010-s03@example.com"
USER_PASSWORD = "CookbookFlow123!"
USER_NAME = "M010 S03 Browser User"

BOOKS = [
    {
        "title": "Weeknight Classics",
        "author": "GRASP Test Kitchen",
        "recipes": [
            {
                "chapter": "Centerpieces",
                "page_number": 42,
                "text": "Roast Chicken with Herbs\nIngredients\n1 chicken\nherbs\nolives\nMethod\nDry the bird overnight. Roast hot. Rest before carving.",
            },
            {
                "chapter": "Sides",
                "page_number": 117,
                "text": "Braised Greens\nIngredients\ngreens\nstock\ngarlic\nMethod\nWilt the greens. Add stock. Simmer until tender.",
            },
            {
                "chapter": "Centerpieces",
                "page_number": 58,
                "text": "Slow-Roasted Pork Shoulder\nIngredients\npork shoulder\nsalt\npepper\nMethod\nSeason overnight. Roast low and slow. Rest and shred.",
            },
        ],
    },
    {
        "title": "The Dessert Atlas",
        "author": "GRASP Test Kitchen",
        "recipes": [
            {
                "chapter": "Late Course",
                "page_number": 88,
                "text": "Burnt Honey Tart\nIngredients\nhoney\ncream\ncrust\nMethod\nBlind bake the crust. Warm the honey. Finish with flaky salt.",
            },
            {
                "chapter": "Custards",
                "page_number": 91,
                "text": "Vanilla Bean Custard\nIngredients\ncream\nvanilla\neggs\nMethod\nWarm the cream. Temper the eggs. Bake gently until set.",
            },
        ],
    },
]


async def main():
    async with SessionLocal() as session:
        result = await session.exec(select(UserProfile).where(UserProfile.email == USER_EMAIL))
        user = result.first()

        if user is None:
            kitchen = KitchenConfig()
            session.add(kitchen)
            await session.flush()
            password_hash = bcrypt.hashpw(USER_PASSWORD.encode(), bcrypt.gensalt()).decode()
            user = UserProfile(
                name=USER_NAME,
                email=USER_EMAIL,
                rag_owner_key=UserProfile.build_rag_owner_key(USER_EMAIL),
                password_hash=password_hash,
                kitchen_config_id=kitchen.kitchen_config_id,
            )
            session.add(user)
            await session.flush()
        else:
            if not user.password_hash:
                user.password_hash = bcrypt.hashpw(USER_PASSWORD.encode(), bcrypt.gensalt()).decode()
                session.add(user)

        existing_books = await session.exec(select(BookRecord).where(BookRecord.user_id == user.user_id))
        books_by_title = {book.title: book for book in existing_books.all()}

        for book_payload in BOOKS:
            book = books_by_title.get(book_payload["title"])
            if book is None:
                book = BookRecord(
                    user_id=user.user_id,
                    title=book_payload["title"],
                    author=book_payload["author"],
                    total_pages=max(recipe["page_number"] for recipe in book_payload["recipes"]),
                    total_chunks=len(book_payload["recipes"]),
                )
                session.add(book)
                await session.flush()

            existing_chunks = await session.exec(select(CookbookChunk).where(CookbookChunk.book_id == book.book_id))
            existing_texts = {chunk.text for chunk in existing_chunks.all()}

            for recipe in book_payload["recipes"]:
                if recipe["text"] in existing_texts:
                    continue
                session.add(
                    CookbookChunk(
                        book_id=book.book_id,
                        user_id=user.user_id,
                        text=recipe["text"],
                        chunk_type=ChunkType.RECIPE,
                        chapter=recipe["chapter"],
                        page_number=recipe["page_number"],
                        token_count=len(recipe["text"].split()),
                        pinecone_upserted=False,
                    )
                )

        await session.commit()

    print(f"seeded_user_email={USER_EMAIL}")
    print(f"seeded_user_password={USER_PASSWORD}")


if __name__ == '__main__':
    asyncio.run(main())
