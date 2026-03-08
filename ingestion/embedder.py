"""
ingestion/embedder.py
Phase 2e: OpenAI text-embedding-3-small (1536 dims) + Pinecone upsert.
user_id in every chunk metadata for per-chef RAG isolation.
"""

import uuid
from core.settings import get_settings

settings = get_settings()


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
    from models.ingestion import CookbookChunk
    from models.enums import ChunkType

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index_name)

    texts = [c["text"] for c in chunks]
    response = await openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    embeddings = [item.embedding for item in response.data]

    vectors = []
    for chunk_data, embedding in zip(chunks, embeddings):
        chunk_id = str(uuid.uuid4())
        chunk_obj = CookbookChunk(
            chunk_id=uuid.UUID(chunk_id),
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
            "id": chunk_id,
            "values": embedding,
            "metadata": chunk_obj.to_pinecone_metadata(),
        })

    # Upsert in batches of 100 (Pinecone limit)
    batch_size = 100
    for i in range(0, len(vectors), batch_size):
        index.upsert(vectors=vectors[i:i + batch_size])

    await db.commit()
    return len(vectors)
