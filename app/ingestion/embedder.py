"""
ingestion/embedder.py

INTERNAL INFRASTRUCTURE ONLY — Phase 2e: OpenAI text-embedding-3-small (1536 dims) + Pinecone upsert.

After M015 pivot (cookbook de-scope), this module is used only for team/admin
curated cookbook uploads, not user-facing upload flows.

user_id in every chunk metadata enables per-user RAG isolation.
Future: rag_owner_key for stable cross-user shared curated libraries.

See: .gsd/milestones/M015/slices/S03/S03-CONTEXT.md for enrichment contract.
"""

import hashlib
import logging
import uuid

from app.core.settings import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# Safety net for the 8192 token embedding limit.
# ~2000 words ≈ 2600 BPE tokens — only fires for unusually long recipes
# that the state machine keeps whole. Splits on word boundaries.
_MAX_CHUNK_WORDS = 2000

# Embedding batch size — balances throughput vs blast radius on error
_EMBED_BATCH_SIZE = 50


def _split_oversized_chunks(chunks: list[dict]) -> list[dict]:
    """Split chunks that exceed the embedding model's token limit."""
    result = []
    for chunk in chunks:
        words = chunk["text"].split()
        if len(words) <= _MAX_CHUNK_WORDS:
            result.append(chunk)
        else:
            for i in range(0, len(words), _MAX_CHUNK_WORDS):
                part = " ".join(words[i : i + _MAX_CHUNK_WORDS])
                result.append({**chunk, "text": part})
    return result


def _deterministic_chunk_id(book_id: str, chunk_index: int, text: str) -> uuid.UUID:
    """Derive a deterministic UUID from content so re-ingestion overwrites, not duplicates."""
    key = f"{book_id}:{chunk_index}:{text[:200]}"
    return uuid.uuid5(uuid.NAMESPACE_URL, key)


async def embed_and_upsert_chunks(
    chunks: list[dict],
    book_id: str,
    user_id: str,
    db,
) -> int:
    """
    Embed chunks and upsert to Pinecone. Returns count of upserted chunks.
    """
    import asyncio

    from openai import AsyncOpenAI
    from pinecone import Pinecone
    from sqlmodel import select

    from app.models.enums import ChunkType
    from app.models.ingestion import CookbookChunk
    from app.models.user import UserProfile

    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index_name)

    chunks = _split_oversized_chunks(chunks)

    # Clean up old chunks for this book (idempotent re-ingestion)
    old_chunks = await db.execute(select(CookbookChunk).where(CookbookChunk.book_id == uuid.UUID(book_id)))
    for old in old_chunks.scalars().all():
        await db.delete(old)

    user_result = await db.execute(select(UserProfile).where(UserProfile.user_id == uuid.UUID(user_id)))
    user = user_result.scalars().first()
    rag_owner_key = user.rag_owner_key if user else UserProfile.build_rag_owner_key(user_id)

    total_upserted = 0

    async with AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0) as openai_client:
        fallback_sem = asyncio.Semaphore(10)

        async def _embed_single_text(text: str, chunk_index: int) -> list[float] | None:
            async with fallback_sem:
                try:
                    resp = await openai_client.embeddings.create(
                        model="text-embedding-3-small",
                        input=[text],
                    )
                    return resp.data[0].embedding
                except Exception as chunk_err:
                    logger.warning("Skipping chunk %d: %s", chunk_index, chunk_err)
                    return None

        for batch_start in range(0, len(chunks), _EMBED_BATCH_SIZE):
            batch = chunks[batch_start : batch_start + _EMBED_BATCH_SIZE]
            texts = [c["text"] for c in batch]

            # Embed batch — on failure, fall back to per-chunk embedding
            try:
                response = await openai_client.embeddings.create(
                    model="text-embedding-3-small",
                    input=texts,
                )
                embeddings = [item.embedding for item in response.data]
            except Exception as e:
                logger.warning("Batch embedding failed (%d chunks), falling back to per-chunk: %s", len(batch), e)
                fallback_results = await asyncio.gather(
                    *(_embed_single_text(text, batch_start + i) for i, text in enumerate(texts)),
                    return_exceptions=True,
                )
                embeddings = []
                for i, result in enumerate(fallback_results):
                    if isinstance(result, BaseException):
                        logger.warning("Skipping chunk %d: %s", batch_start + i, result)
                        embeddings.append(None)
                    else:
                        embeddings.append(result)

            vectors = []
            for i, (chunk_data, embedding) in enumerate(zip(batch, embeddings)):
                if embedding is None:
                    continue

                chunk_id = _deterministic_chunk_id(book_id, batch_start + i, chunk_data["text"])
                chunk_obj = CookbookChunk(
                    chunk_id=chunk_id,
                    book_id=uuid.UUID(book_id),
                    user_id=uuid.UUID(user_id),
                    text=chunk_data["text"],
                    chunk_type=ChunkType(chunk_data["chunk_type"]),
                    chapter=chunk_data.get("chapter", ""),
                    page_number=chunk_data.get("page_number", 0),
                    token_count=len(chunk_data["text"].split()),
                    pinecone_upserted=True,
                )
                db.add(chunk_obj)

                vectors.append(
                    {
                        "id": str(chunk_id),
                        "values": embedding,
                        "metadata": {
                            **chunk_obj.to_pinecone_metadata(),
                            "rag_owner_key": rag_owner_key,
                        },
                    }
                )

            # Upsert to Pinecone then commit DB (correct order: vector store first)
            if vectors:
                pinecone_batch = 100
                for i in range(0, len(vectors), pinecone_batch):
                    await asyncio.wait_for(
                        asyncio.to_thread(index.upsert, vectors=vectors[i : i + pinecone_batch]),
                        timeout=60,
                    )
                await db.commit()
                total_upserted += len(vectors)

    return total_upserted
