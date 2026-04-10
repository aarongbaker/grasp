"""
app/db/session.py
Async SQLAlchemy engine and session factory.

get_session() is a FastAPI dependency — yields an AsyncSession per request.
create_db_and_tables() is called once at startup by the lifespan hook.
engine is exposed for disposal at shutdown.

Why asyncpg + SQLAlchemy instead of psycopg3?
  - FastAPI is async-native. asyncpg is the fastest async Postgres driver
    and integrates directly with SQLAlchemy's async session API.
  - LangGraph's PostgresSaver uses psycopg3 (sync) internally — it has its
    own connection management and cannot share the SQLAlchemy pool.
  - Keeping two separate drivers avoids connection pool collisions and
    prevents LangGraph's sync driver from blocking the asyncio event loop.

echo=False in production: SQLAlchemy's echo=True logs every SQL statement,
which is useful for debugging but produces enormous output in production.
Set LOG_LEVEL=DEBUG to see queries via SQLAlchemy's own logging.

expire_on_commit=False: by default SQLAlchemy expires all attributes after
commit(), forcing a DB reload on next access. This is undesirable in async
context — await db.commit() followed by return session_obj would trigger
a lazy load that fails because the session may have closed. expire_on_commit=False
preserves attribute values after commit, making return-after-commit safe.
"""

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.settings import get_settings

settings = get_settings()

# Create the async engine once at module import time.
# All route requests share this engine's connection pool.
# echo=False keeps logs clean in production — queries are logged at DEBUG level.
engine = create_async_engine(settings.database_url, echo=False)

# Create the session factory once — reused by get_session() on every request.
# Previously this was recreated per-call, which was wasteful.
# AsyncSession: async context manager that yields one DB session.
# expire_on_commit=False: see module docstring for why this is required.
SessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def create_db_and_tables():
    """Create all SQLModel tables. Called once at startup.

    Only used in development and tests — production uses Alembic migrations.
    The import statements force SQLModel to discover all table models before
    calling create_all(). Without these imports, SQLModel.metadata would
    not know about those tables and would skip creating them.

    The noqa: F401 comments suppress "imported but unused" warnings — these
    imports are purely for their side effect of registering table metadata.
    """
    # Import all table models to register them with SQLModel metadata
    import app.models.authored_recipe  # noqa: F401
    import app.models.ingestion  # noqa: F401
    import app.models.invite  # noqa: F401
    import app.models.session  # noqa: F401
    import app.models.user  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session():
    """FastAPI dependency. Yields one AsyncSession per request.

    Used as `Depends(get_session)` or via the `DBSession` alias in
    core/deps.py. FastAPI creates the session at the start of the request
    and ensures it is closed after the response is sent, even if an
    exception is raised.

    The `async with SessionLocal() as session` pattern handles both
    commit and rollback: if the request handler raises an exception,
    SQLAlchemy rolls back automatically before closing.
    """
    async with SessionLocal() as session:
        yield session
