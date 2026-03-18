"""add celery_task_id to sessions

Revision ID: a1b2c3d4e5f6
Revises: 46264411096a
Create Date: 2026-03-17 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "46264411096a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("sessions", sa.Column("celery_task_id", sa.String(), nullable=True))
    op.execute("ALTER TYPE sessionstatus ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("sessions", "celery_task_id")
    # Note: Postgres does not support removing enum values; 'cancelled' remains in the type
