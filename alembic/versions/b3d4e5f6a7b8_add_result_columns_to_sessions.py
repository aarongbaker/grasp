"""add result_recipes and result_schedule to sessions

Revision ID: b3d4e5f6a7b8
Revises: 1c8c1e18507a
Create Date: 2026-03-18 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "1c8c1e18507a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add result columns for persisting full pipeline output."""
    op.add_column("sessions", sa.Column("result_recipes", sa.JSON(), nullable=True))
    op.add_column("sessions", sa.Column("result_schedule", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove result columns."""
    op.drop_column("sessions", "result_schedule")
    op.drop_column("sessions", "result_recipes")
