"""
ingestion/embedder.py
Phase 2e: OpenAI text-embedding-3-small (1536 dims) + Pinecone upsert.
user_id in every chunk metadata for per-chef RAG isolation.
"""

import hashlib
import logging
import uuid
from core.settings import get_settings

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
                part = " ".join(words[i:i + _MAX_CHUNK_WORDS])
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
    from openai import AsyncOpenAI
    from pinecone import Pinecone
    from sqlmodel import select
    from models.ingestion import CookbookChunk
    from models.enums import ChunkType

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index_name)

    chunks = _split_oversized_chunks(chunks)

    # Clean up old chunks for this book (idempotent re-ingestion)
    old_chunks = await db.execute(
        select(CookbookChunk).where(CookbookChunk.book_id == uuid.UUID(book_id))
    )
    for old in old_chunks.scalars().all():
        await db.delete(old)

    total_upserted = 0

    for batch_start in range(0, len(chunks), _EMBED_BATCH_SIZE):
        batch = chunks[batch_start:batch_start + _EMBED_BATCH_SIZE]
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
            embeddings = []
            for i, text in enumerate(texts):
                try:
                    resp = await openai_client.embeddings.create(
                        model="text-embedding-3-small",
                        input=[text],
                    )
                    embeddings.append(resp.data[0].embedding)
                except Exception as chunk_err:
                    logger.warning("Skipping chunk %d: %s", batch_start + i, chunk_err)
                    embeddings.append(None)

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

            vectors.append({
                "id": str(chunk_id),
                "values": embedding,
                "metadata": chunk_obj.to_pinecone_metadata(),
            })

        # Upsert to Pinecone then commit DB (correct order: vector store first)
        if vectors:
            pinecone_batch = 100
            for i in range(0, len(vectors), pinecone_batch):
                index.upsert(vectors=vectors[i:i + pinecone_batch])
            await db.commit()
            total_upserted += len(vectors)

    return total_upserted
