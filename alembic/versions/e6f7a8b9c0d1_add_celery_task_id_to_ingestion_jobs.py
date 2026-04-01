"""add celery_task_id to ingestion_jobs

Revision ID: e6f7a8b9c0d1
Revises: c4d5e6f7a8b9
Create Date: 2026-04-01 10:35:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("ingestion_jobs", sa.Column("celery_task_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("ingestion_jobs", "celery_task_id")
