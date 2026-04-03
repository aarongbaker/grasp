"""add invite expiry column

Revision ID: f1a2b3c4d5e6
Revises: e2f3a4b5c6d7
Create Date: 2026-04-03 16:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invites", sa.Column("expires_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE invites SET expires_at = created_at + INTERVAL '7 days' WHERE expires_at IS NULL")
    op.alter_column("invites", "expires_at", nullable=False)


def downgrade() -> None:
    op.drop_column("invites", "expires_at")
