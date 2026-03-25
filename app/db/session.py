"""
app/db/session.py
Async SQLAlchemy engine and session factory.

get_session() is a FastAPI dependency — yields an AsyncSession per request.
create_db_and_tables() is called once at startup by the lifespan hook.
engine is exposed for disposal at shutdown.
"""

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.settings import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False)

# Create sessionmaker once — reused by get_session() on every request.
# Previously this was recreated per-call, which is wasteful.
SessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def create_db_and_tables():
    """Create all SQLModel tables. Called once at startup."""
    # Import all table models to register them with SQLModel metadata
    import app.models.ingestion  # noqa: F401
    import app.models.session  # noqa: F401
    import app.models.user  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session():
    """FastAPI dependency. Yields one AsyncSession per request."""
    async with SessionLocal() as session:
        yield session
