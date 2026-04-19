"""add generation billing ledger table

Revision ID: 1776217588_generation_billing_ledger
Revises: 9b1d7e6c4a2f
Create Date: 2026-04-14 21:46:28.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "1776217588_generation_billing_ledger"
down_revision = "9b1d7e6c4a2f"
branch_labels = None
depends_on = None


generation_billing_state_enum = postgresql.ENUM(
    "ready",
    "skipped",
    "charge_pending",
    "charged",
    "charge_failed",
    name="generationbillingstate",
    create_type=False,
)
generation_billing_provider_enum = postgresql.ENUM(
    "app",
    "stripe",
    name="generationbillingprovider",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    generation_billing_state_enum.create(bind, checkfirst=True)
    generation_billing_provider_enum.create(bind, checkfirst=True)

    op.create_table(
        "generation_billing_records",
        sa.Column("generation_billing_record_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_status", sa.String(), nullable=False),
        sa.Column("billing_state", generation_billing_state_enum, nullable=False),
        sa.Column("provider", generation_billing_provider_enum, nullable=False),
        sa.Column("provider_charge_ref", sa.String(length=255), nullable=True),
        sa.Column("provider_error_code", sa.String(length=100), nullable=True),
        sa.Column("provider_error_message", sa.String(length=500), nullable=True),
        sa.Column("billing_reason", sa.String(length=200), nullable=True),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_usage_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("billing_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("charge_attempted_at", sa.DateTime(), nullable=True),
        sa.Column("charged_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("generation_billing_record_id"),
        sa.UniqueConstraint("session_id", name="uq_generation_billing_records_session_id"),
    )
    op.create_index(
        "ix_generation_billing_records_session_id",
        "generation_billing_records",
        ["session_id"],
        unique=True,
    )
    op.create_index("ix_generation_billing_records_user_id", "generation_billing_records", ["user_id"], unique=False)
    op.create_index(
        "ix_generation_billing_records_billing_state",
        "generation_billing_records",
        ["billing_state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_generation_billing_records_billing_state", table_name="generation_billing_records")
    op.drop_index("ix_generation_billing_records_user_id", table_name="generation_billing_records")
    op.drop_index("ix_generation_billing_records_session_id", table_name="generation_billing_records")
    op.drop_table("generation_billing_records")

    bind = op.get_bind()
    generation_billing_provider_enum.drop(bind, checkfirst=True)
    generation_billing_state_enum.drop(bind, checkfirst=True)
