"""add authored_recipes table

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-04-01 14:25:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create authored_recipes for private chef-authored drafts."""
    op.create_table(
        "authored_recipes",
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("cuisine", sa.String(), nullable=False),
        sa.Column("authored_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("recipe_id"),
    )
    op.create_index(op.f("ix_authored_recipes_title"), "authored_recipes", ["title"], unique=False)
    op.create_index(op.f("ix_authored_recipes_user_id"), "authored_recipes", ["user_id"], unique=False)


def downgrade() -> None:
    """Drop authored_recipes table."""
    op.drop_index(op.f("ix_authored_recipes_user_id"), table_name="authored_recipes")
    op.drop_index(op.f("ix_authored_recipes_title"), table_name="authored_recipes")
    op.drop_table("authored_recipes")
