"""add rag_owner_key to user_profiles

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-03-25 14:55:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("rag_owner_key", sa.String(), nullable=True),
    )

    op.execute(
        """
        UPDATE user_profiles
        SET rag_owner_key = 'email:' || regexp_replace(lower(trim(email)), '[^a-z0-9]+', '-', 'g')
        WHERE rag_owner_key IS NULL OR rag_owner_key = '';
        """
    )

    op.alter_column("user_profiles", "rag_owner_key", nullable=False)
    op.create_index("ix_user_profiles_rag_owner_key", "user_profiles", ["rag_owner_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_profiles_rag_owner_key", table_name="user_profiles")
    op.drop_column("user_profiles", "rag_owner_key")
