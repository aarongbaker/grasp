"""
Alembic migration environment.

Reads the database URL from core/settings.py and uses SQLModel metadata
for autogenerate support. All table models must be imported before
target_metadata is set.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Import all table models to register them with SQLModel.metadata
import models.ingestion  # noqa: F401
import models.session  # noqa: F401
import models.user  # noqa: F401
from alembic import context

# Alembic Config object
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set sqlalchemy.url from settings if not already set via CLI
if not config.get_main_option("sqlalchemy.url"):
    from core.settings import get_settings

    settings = get_settings()
    # Alembic needs a sync driver — use psycopg3 (already installed)
    sync_url = settings.database_url.replace("+asyncpg", "+psycopg")
    config.set_main_option("sqlalchemy.url", sync_url)

target_metadata = SQLModel.metadata

# LangGraph checkpoint tables are managed by PostgresSaver, not Alembic
EXCLUDE_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def include_name(name, type_, parent_names):
    """Exclude LangGraph checkpoint tables from autogenerate."""
    if type_ == "table":
        return name not in EXCLUDE_TABLES
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_name=include_name,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
