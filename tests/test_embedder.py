import sys
import types
import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import ChunkType
from app.models.user import UserProfile


def _embedding_response(*vectors):
    return SimpleNamespace(data=[SimpleNamespace(embedding=vector) for vector in vectors])


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarResult(self._rows)


class StubDB:
    def __init__(self, user):
        self.user = user
        self.deleted = []
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        statement = str(stmt).lower()
        if "from cookbook_chunks" in statement:
            return _ExecuteResult([])
        if "from user_profiles" in statement:
            return _ExecuteResult([self.user])
        raise AssertionError(f"Unexpected statement: {stmt}")

    async def delete(self, obj):
        self.deleted.append(obj)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class FakeIndex:
    def __init__(self):
        self.upserts = []

    def upsert(self, *, vectors):
        self.upserts.append(vectors)


class FakePinecone:
    last_index = None

    def __init__(self, api_key):
        self.api_key = api_key

    def Index(self, name):
        FakePinecone.last_index = FakeIndex()
        return FakePinecone.last_index


class FakeAsyncOpenAI:
    instances = []
    create_side_effect = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.entered = False
        self.exited = False
        self.call_inputs = []
        FakeAsyncOpenAI.instances.append(self)
        self.embeddings = SimpleNamespace(create=self._create)

    async def _create(self, *, model, input):
        self.call_inputs.append({"model": model, "input": input})
        return await FakeAsyncOpenAI.create_side_effect(model=model, input=input)

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


def _stub_modules():
    openai = types.ModuleType("openai")
    openai.AsyncOpenAI = FakeAsyncOpenAI

    pinecone = types.ModuleType("pinecone")
    pinecone.Pinecone = FakePinecone

    return {"openai": openai, "pinecone": pinecone}


def _sample_chunks():
    return [
        {"text": "first chunk", "chunk_type": ChunkType.RECIPE.value, "page_number": 1},
        {"text": "second chunk", "chunk_type": ChunkType.TIP.value, "page_number": 2},
    ]


@pytest.mark.asyncio
async def test_embed_and_upsert_chunks_uses_single_async_openai_client_per_invocation():
    from app.ingestion.embedder import embed_and_upsert_chunks

    user_id = uuid.uuid4()
    user = UserProfile(
        user_id=user_id,
        name="Chef",
        email="chef@test.com",
        rag_owner_key="owner-key",
    )
    db = StubDB(user)
    FakeAsyncOpenAI.instances = []
    FakeAsyncOpenAI.create_side_effect = lambda **kwargs: pytest.fail("side effect must be async")

    async def _batch_success(*, model, input):
        assert model == "text-embedding-3-small"
        assert len(input) == 2
        return _embedding_response([0.1, 0.2], [0.3, 0.4])

    FakeAsyncOpenAI.create_side_effect = _batch_success

    with pytest.MonkeyPatch.context() as mp:
        for module_name, module in _stub_modules().items():
            mp.setitem(sys.modules, module_name, module)
        count = await embed_and_upsert_chunks(_sample_chunks(), str(uuid.uuid4()), str(user_id), db)

    assert count == 2
    assert len(FakeAsyncOpenAI.instances) == 1
    client = FakeAsyncOpenAI.instances[0]
    assert client.entered is True
    assert client.exited is True
    assert len(client.call_inputs) == 1
    assert db.commits == 1
    assert len(db.added) == 2
    assert len(FakePinecone.last_index.upserts) == 1


@pytest.mark.asyncio
async def test_embed_and_upsert_chunks_reuses_context_managed_client_for_fallback_calls():
    from app.ingestion.embedder import embed_and_upsert_chunks

    user_id = uuid.uuid4()
    user = UserProfile(
        user_id=user_id,
        name="Chef",
        email="chef@test.com",
        rag_owner_key="owner-key",
    )
    db = StubDB(user)
    FakeAsyncOpenAI.instances = []

    async def _fallback_path(*, model, input):
        if len(input) == 2:
            raise RuntimeError("batch failed")
        text = input[0]
        if text == "first chunk":
            return _embedding_response([0.1, 0.2])
        return _embedding_response([0.3, 0.4])

    FakeAsyncOpenAI.create_side_effect = _fallback_path

    with pytest.MonkeyPatch.context() as mp:
        for module_name, module in _stub_modules().items():
            mp.setitem(sys.modules, module_name, module)
        count = await embed_and_upsert_chunks(_sample_chunks(), str(uuid.uuid4()), str(user_id), db)

    assert count == 2
    assert len(FakeAsyncOpenAI.instances) == 1
    client = FakeAsyncOpenAI.instances[0]
    assert client.entered is True
    assert client.exited is True
    assert len(client.call_inputs) == 3
    assert client.call_inputs[0]["input"] == ["first chunk", "second chunk"]
    assert client.call_inputs[1]["input"] == ["first chunk"]
    assert client.call_inputs[2]["input"] == ["second chunk"]
