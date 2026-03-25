"""fix cancelled enum case to uppercase

Revision ID: c4d5e6f7a8b9
Revises: b3d4e5f6a7b8
Create Date: 2026-03-19 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename lowercase 'cancelled' to uppercase 'CANCELLED' to match enum convention."""
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_enum
                WHERE enumlabel = 'cancelled'
                AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'sessionstatus')
            ) THEN
                ALTER TYPE sessionstatus RENAME VALUE 'cancelled' TO 'CANCELLED';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    """Revert to lowercase (not recommended)."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_enum
                WHERE enumlabel = 'CANCELLED'
                AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'sessionstatus')
            ) THEN
                ALTER TYPE sessionstatus RENAME VALUE 'CANCELLED' TO 'cancelled';
            END IF;
        END $$;
        """
    )
