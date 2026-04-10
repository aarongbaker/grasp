"""
ingestion/embedder.py

INTERNAL INFRASTRUCTURE ONLY — Phase 2e: OpenAI text-embedding-3-small (1536 dims) + Pinecone upsert.

After M015 pivot (cookbook de-scope), this module is used only for team/admin
curated cookbook uploads, not user-facing upload flows.

Embedding model: text-embedding-3-small (1536 dimensions)
  Chosen for cost/quality balance at V1. text-embedding-3-large (3072 dims)
  would improve retrieval at ~6x the cost. Switch by updating the model
  name — Pinecone index dimensionality must match.

user_id in every chunk metadata enables per-user RAG isolation in Pinecone.
The enricher filters by user_id on retrieval so one user's cookbook content
never appears in another user's enrichment results.

rag_owner_key: a stable hash of the user's email, used as an alternative
  isolation key that survives user_id changes across DB migrations. Stored
  in Pinecone metadata so the enricher can filter by either key.

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
# that the state machine keeps whole. Splits on word boundaries (not sentence
# boundaries) to stay simple — recipe content won't lose coherence from a
# mid-sentence split at this scale.
_MAX_CHUNK_WORDS = 2000

# Embedding batch size — balances throughput vs blast radius on error.
# 50 chunks per batch: if a batch fails, we fall back to per-chunk embedding
# for 50 chunks, not the entire book. OpenAI's embedding API supports up to
# 2048 inputs per request, but smaller batches give better progress granularity.
_EMBED_BATCH_SIZE = 50


def _split_oversized_chunks(chunks: list[dict]) -> list[dict]:
    """Split chunks that exceed the embedding model's token limit.

    The state machine keeps recipes whole (no word limit), but some historical
    cookbooks have very long recipes. This is the safety net that prevents
    token-limit errors from the OpenAI embeddings API.

    Splits on word boundaries — crude but correct. Recipe chunks split here
    will be retrievable as separate Pinecone vectors, but both halves retain
    the same metadata (book_id, chapter, page_number), so they'll still match
    queries correctly even if neither half alone is a complete recipe.
    """
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
    """Derive a deterministic UUID from content so re-ingestion overwrites, not duplicates.

    Uses uuid5 (name-based UUID from SHA-1) with a composite key of:
      book_id: ties the chunk to a specific book upload
      chunk_index: position within the book (different chunks at same position get different IDs)
      text[:200]: content fingerprint (catches edits to the same chunk position)

    Why not uuid4 (random)?
      Random UUIDs would create duplicate vectors in Pinecone on re-ingestion.
      Deterministic IDs let Pinecone's upsert operation overwrite the existing
      vector instead of duplicating it. This makes re-ingestion idempotent.

    uuid.NAMESPACE_URL is the conventional namespace for URL-like composite keys.
    """
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

    Ordering of operations within each batch:
      1. Embed the batch via OpenAI (or fall back to per-chunk on failure)
      2. Build Pinecone vector dicts + CookbookChunk ORM objects
      3. Upsert to Pinecone first — if the DB commit fails, re-ingestion
         will just overwrite the Pinecone vectors (deterministic IDs)
      4. Commit DB records — if the upsert fails, we don't commit stale
         DB records that claim pinecone_upserted=True

    Why Pinecone first?
      Pinecone upsert is idempotent (deterministic IDs). If we commit to DB
      first and then Pinecone fails, we'd have DB records claiming chunks are
      in Pinecone when they're not. Pinecone-first means a partial failure
      leaves Pinecone ahead of the DB, which re-ingestion corrects cleanly.

    Batch fallback strategy:
      If a batch embedding call fails, we fall back to per-chunk embedding
      with a concurrency limit (Semaphore(10)). Per-chunk failures are skipped
      individually — a bad chunk doesn't block the rest of the book.
      This is logged at WARNING so admin monitoring can catch systematic issues
      (e.g. the book contains a chunk that consistently exceeds the token limit).

    Old chunk cleanup:
      At the start, existing CookbookChunk rows for this book_id are deleted
      from the DB. This ensures re-ingestion is fully idempotent — no orphaned
      rows from a previous run that had different chunk boundaries.
      Pinecone vectors are overwritten via upsert (no explicit delete needed
      there because chunk IDs are deterministic).
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

    # Apply safety-net word limit before processing
    chunks = _split_oversized_chunks(chunks)

    # Clean up old chunks for this book (idempotent re-ingestion).
    # Delete DB rows first — if Pinecone upsert fails later, re-running
    # the ingestion will regenerate the DB rows from scratch.
    old_chunks = await db.execute(select(CookbookChunk).where(CookbookChunk.book_id == uuid.UUID(book_id)))
    for old in old_chunks.scalars().all():
        await db.delete(old)

    # Resolve rag_owner_key for Pinecone metadata.
    # Falls back to build_rag_owner_key(user_id) if the user record is not found
    # (defensive — should not happen in normal ingestion flow).
    user_result = await db.execute(select(UserProfile).where(UserProfile.user_id == uuid.UUID(user_id)))
    user = user_result.scalars().first()
    rag_owner_key = user.rag_owner_key if user else UserProfile.build_rag_owner_key(user_id)

    total_upserted = 0

    async with AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0) as openai_client:
        # Semaphore limits concurrent per-chunk fallback calls to 10 simultaneous
        # requests — prevents overwhelming the OpenAI API rate limit during fallback.
        fallback_sem = asyncio.Semaphore(10)

        async def _embed_single_text(text: str, chunk_index: int) -> list[float] | None:
            """Embed a single text with rate-limited concurrency. Returns None on failure."""
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

            # Primary path: batch embedding (most efficient)
            try:
                response = await openai_client.embeddings.create(
                    model="text-embedding-3-small",
                    input=texts,
                )
                embeddings = [item.embedding for item in response.data]
            except Exception as e:
                # Batch failed — fall back to per-chunk with concurrency limit.
                # This handles transient failures (timeouts, rate limits) and
                # edge cases where one chunk causes a batch rejection.
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
                    continue  # Skip chunks that failed to embed

                chunk_id = _deterministic_chunk_id(book_id, batch_start + i, chunk_data["text"])

                # Create DB record tracking the chunk metadata.
                # token_count is approximated as word count — accurate enough
                # for storage/billing estimates, not exact BPE token count.
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
                            # to_pinecone_metadata() returns standard fields:
                            # book_id, user_id, chunk_type, chapter, page_number, text (truncated)
                            **chunk_obj.to_pinecone_metadata(),
                            # rag_owner_key is added here (not in the model method) because
                            # it's derived from the UserProfile, which the chunk model
                            # doesn't have a reference to.
                            "rag_owner_key": rag_owner_key,
                        },
                    }
                )

            # Upsert to Pinecone in sub-batches of 100 (Pinecone's recommended max).
            # Then commit DB records — Pinecone first for idempotency (see module docstring).
            if vectors:
                pinecone_batch = 100
                for i in range(0, len(vectors), pinecone_batch):
                    await asyncio.wait_for(
                        asyncio.to_thread(index.upsert, vectors=vectors[i : i + pinecone_batch]),
                        timeout=60,  # 60s timeout per Pinecone upsert batch
                    )
                await db.commit()
                total_upserted += len(vectors)

    return total_upserted
