"""add burners to kitchen_configs

Revision ID: 2a4b6c8d0e1f
Revises: f1a2b3c4d5e6
Create Date: 2026-04-08 10:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2a4b6c8d0e1f"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "kitchen_configs",
        sa.Column("burners", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("kitchen_configs", "burners")
