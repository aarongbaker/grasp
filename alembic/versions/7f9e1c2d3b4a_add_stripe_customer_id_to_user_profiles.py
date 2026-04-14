"""add stripe customer id to user profiles

Revision ID: 7f9e1c2d3b4a
Revises: 9b1d7e6c4a2f
Create Date: 2026-04-13 18:08:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7f9e1c2d3b4a"
down_revision: Union[str, Sequence[str], None] = "9b1d7e6c4a2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=False, server_default=""),
    )
    op.create_index("ix_user_profiles_stripe_customer_id", "user_profiles", ["stripe_customer_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_profiles_stripe_customer_id", table_name="user_profiles")
    op.drop_column("user_profiles", "stripe_customer_id")
