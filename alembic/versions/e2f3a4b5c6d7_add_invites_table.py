"""add invites table

Revision ID: e2f3a4b5c6d7
Revises: d5e6f7a8b9c0
Create Date: 2026-04-03 11:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e2f3a4b5c6d7"
down_revision = "b69445a5009d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create invites table for controlled registration."""
    op.create_table(
        "invites",
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_index("ix_invites_code", "invites", ["code"])
    op.create_index("ix_invites_email", "invites", ["email"])


def downgrade() -> None:
    """Drop invites table."""
    op.drop_index("ix_invites_email", table_name="invites")
    op.drop_index("ix_invites_code", table_name="invites")
    op.drop_table("invites")
