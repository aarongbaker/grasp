import sys
import types
import uuid
from unittest.mock import AsyncMock, patch

import pytest

import app.models.user  # noqa: F401
from app.models.enums import IngestionStatus
from app.models.ingestion import IngestionJob
from app.workers.tasks import _ingest_async


class StubDB:
    def __init__(self, job):
        self.job = job
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit_after = None
        self.fail_commit_once = False

    async def get(self, model_class, pk):
        return self.job

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1
        if self.fail_commit_after is not None and self.commits >= self.fail_commit_after:
            if self.fail_commit_once:
                self.fail_commit_after = None
            raise RuntimeError("simulated commit failure")

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        return None


class StubSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class StubSessionFactory:
    def __init__(self, db):
        self.db = db

    def __call__(self, *args, **kwargs):
        return StubSessionContext(self.db)


class StubEngine:
    async def dispose(self):
        return None


def _stub_ingestion_modules():
    classifier = types.ModuleType("app.ingestion.classifier")
    classifier.classify_document = AsyncMock(return_value="cookbook")

    embedder = types.ModuleType("app.ingestion.embedder")
    embedder.embed_and_upsert_chunks = AsyncMock(return_value=1)

    state_machine = types.ModuleType("app.ingestion.state_machine")
    state_machine.run_state_machine = lambda pages: [{"text": "recipe", "chunk_type": "recipe"}]

    return {
        "app.ingestion.classifier": classifier,
        "app.ingestion.embedder": embedder,
        "app.ingestion.state_machine": state_machine,
    }


@pytest.mark.asyncio
async def test_ingest_async_records_progress_and_completion():
    job = IngestionJob(
        job_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status=IngestionStatus.PENDING,
        book_statuses=[],
    )
    db = StubDB(job)

    async def fake_rasterise(pdf_bytes, book_id, user_id, db_session, progress_callback=None):
        await progress_callback(1, 3)
        await progress_callback(3, 3)
        return [
            {"page_number": 1, "text": "Page one", "confidence": 0.9, "page_hash": "a"},
            {"page_number": 2, "text": "Page two", "confidence": 0.9, "page_hash": "b"},
            {"page_number": 3, "text": "Page three", "confidence": 0.9, "page_hash": "c"},
        ]

    stub_modules = _stub_ingestion_modules()
    rasteriser = types.ModuleType("app.ingestion.rasteriser")
    rasteriser.rasterise_and_ocr_pdf = fake_rasterise
    stub_modules["app.ingestion.rasteriser"] = rasteriser

    with (
        patch.dict(sys.modules, stub_modules),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=StubEngine()),
        patch("sqlalchemy.orm.sessionmaker", return_value=StubSessionFactory(db)),
    ):
        await _ingest_async(str(job.job_id), str(job.user_id), b"%PDF", "cookbook.pdf")

    assert job.status == IngestionStatus.COMPLETE
    assert job.book_statuses[0]["phase"] == "complete"
    assert job.book_statuses[0]["pages_total"] == 3
    assert job.book_statuses[0]["chunks_total"] == 1
    assert job.book_statuses[0]["embedded_chunks"] == 1


@pytest.mark.asyncio
async def test_ingest_async_marks_failure_with_phase():
    job = IngestionJob(
        job_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status=IngestionStatus.PENDING,
        book_statuses=[],
    )
    db = StubDB(job)

    stub_modules = _stub_ingestion_modules()
    rasteriser = types.ModuleType("app.ingestion.rasteriser")
    rasteriser.rasterise_and_ocr_pdf = AsyncMock(side_effect=RuntimeError("ocr timeout"))
    stub_modules["app.ingestion.rasteriser"] = rasteriser

    with (
        patch.dict(sys.modules, stub_modules),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=StubEngine()),
        patch("sqlalchemy.orm.sessionmaker", return_value=StubSessionFactory(db)),
    ):
        await _ingest_async(str(job.job_id), str(job.user_id), b"%PDF", "cookbook.pdf")

    assert job.status == IngestionStatus.FAILED
    assert job.failed == 1
    assert job.book_statuses[0]["phase"] == "failed"
    assert "ocr timeout" in job.book_statuses[0]["error"]


@pytest.mark.asyncio
async def test_ingest_async_recovers_after_commit_failure_and_marks_job_failed():
    job = IngestionJob(
        job_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status=IngestionStatus.PENDING,
        book_statuses=[],
    )
    db = StubDB(job)
    db.fail_commit_after = 4
    db.fail_commit_once = True

    async def fake_rasterise(pdf_bytes, book_id, user_id, db_session, progress_callback=None):
        await progress_callback(1, 3)
        return [{"page_number": 1, "text": "Page one", "confidence": 0.9, "page_hash": "a"}]

    stub_modules = _stub_ingestion_modules()
    rasteriser = types.ModuleType("app.ingestion.rasteriser")
    rasteriser.rasterise_and_ocr_pdf = fake_rasterise
    stub_modules["app.ingestion.rasteriser"] = rasteriser

    with (
        patch.dict(sys.modules, stub_modules),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=StubEngine()),
        patch("sqlalchemy.orm.sessionmaker", return_value=StubSessionFactory(db)),
    ):
        await _ingest_async(str(job.job_id), str(job.user_id), b"%PDF", "cookbook.pdf")

    assert db.rollbacks == 1
    assert job.status == IngestionStatus.FAILED
    assert job.failed == 1
    assert job.book_statuses[0]["phase"] == "failed"
    assert "simulated commit failure" in job.book_statuses[0]["error"]
