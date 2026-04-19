"""Merge billing, marketplace, and stripe heads into a single head

Revision ID: 20260419_merge_heads
Revises: 1f4d2c8b9a01_generation_funding_ledger, 7f9e1c2d3b4a, 8c9d0e1f2a3b
Create Date: 2026-04-19 13:38:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260419_merge_heads'
down_revision = ('1f4d2c8b9a01_generation_funding_ledger', '7f9e1c2d3b4a', '8c9d0e1f2a3b')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
