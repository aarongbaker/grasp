"""add generation funding ledger

Revision ID: 1f4d2c8b9a01_generation_funding_ledger
Revises: 1776217588_generation_billing_ledger
Create Date: 2026-04-17 19:12:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "1f4d2c8b9a01_generation_funding_ledger"
down_revision = "1776217588_generation_billing_ledger"
branch_labels = None
depends_on = None


generation_funding_grant_type_enum = postgresql.ENUM(
    "subscription_credit",
    "prepaid_balance",
    name="generationfundinggranttype",
    create_type=False,
)
generation_funding_grant_source_enum = postgresql.ENUM(
    "subscription",
    "pack",
    "admin",
    "migration",
    name="generationfundinggrantsource",
    create_type=False,
)
generation_funding_grant_state_enum = postgresql.ENUM(
    "active",
    "exhausted",
    "expired",
    "revoked",
    name="generationfundinggrantstate",
    create_type=False,
)
generation_funding_ledger_entry_kind_enum = postgresql.ENUM(
    "credit",
    "debit",
    "adjustment",
    name="generationfundingledgerentrykind",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    generation_funding_grant_type_enum.create(bind, checkfirst=True)
    generation_funding_grant_source_enum.create(bind, checkfirst=True)
    generation_funding_grant_state_enum.create(bind, checkfirst=True)
    generation_funding_ledger_entry_kind_enum.create(bind, checkfirst=True)

    op.add_column(
        "user_profiles",
        sa.Column("monthly_free_generations_remaining", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "generation_funding_grants",
        sa.Column("generation_funding_grant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("grant_type", generation_funding_grant_type_enum, nullable=False),
        sa.Column("source", generation_funding_grant_source_enum, nullable=False),
        sa.Column("grant_state", generation_funding_grant_state_enum, nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("remaining_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=20), nullable=False, server_default="generation"),
        sa.Column("priority_bucket", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cycle_key", sa.String(length=100), nullable=True),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("generation_funding_grant_id"),
    )
    op.create_index("ix_generation_funding_grants_user_id", "generation_funding_grants", ["user_id"], unique=False)
    op.create_index("ix_generation_funding_grants_grant_state", "generation_funding_grants", ["grant_state"], unique=False)

    op.add_column("generation_billing_records", sa.Column("funding_grant_id", sa.Uuid(), nullable=True))
    op.add_column("generation_billing_records", sa.Column("billing_source_type", sa.String(length=50), nullable=True))
    op.create_foreign_key(
        "fk_generation_billing_records_funding_grant_id",
        "generation_billing_records",
        "generation_funding_grants",
        ["funding_grant_id"],
        ["generation_funding_grant_id"],
    )
    op.create_index(
        "ix_generation_billing_records_funding_grant_id",
        "generation_billing_records",
        ["funding_grant_id"],
        unique=False,
    )

    op.create_table(
        "generation_funding_ledger_entries",
        sa.Column("generation_funding_ledger_entry_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("generation_billing_record_id", sa.Uuid(), nullable=True),
        sa.Column("funding_grant_id", sa.Uuid(), nullable=True),
        sa.Column("entry_kind", generation_funding_ledger_entry_kind_enum, nullable=False),
        sa.Column("funding_source_type", sa.String(length=50), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("balance_after", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["generation_billing_record_id"], ["generation_billing_records.generation_billing_record_id"]),
        sa.ForeignKeyConstraint(["funding_grant_id"], ["generation_funding_grants.generation_funding_grant_id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("generation_funding_ledger_entry_id"),
        sa.UniqueConstraint("session_id", name="uq_generation_funding_ledger_entries_session_id"),
    )
    op.create_index(
        "ix_generation_funding_ledger_entries_user_id",
        "generation_funding_ledger_entries",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_generation_funding_ledger_entries_generation_billing_record_id",
        "generation_funding_ledger_entries",
        ["generation_billing_record_id"],
        unique=False,
    )
    op.create_index(
        "ix_generation_funding_ledger_entries_funding_grant_id",
        "generation_funding_ledger_entries",
        ["funding_grant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_generation_funding_ledger_entries_funding_grant_id", table_name="generation_funding_ledger_entries")
    op.drop_index("ix_generation_funding_ledger_entries_generation_billing_record_id", table_name="generation_funding_ledger_entries")
    op.drop_index("ix_generation_funding_ledger_entries_user_id", table_name="generation_funding_ledger_entries")
    op.drop_table("generation_funding_ledger_entries")

    op.drop_index("ix_generation_billing_records_funding_grant_id", table_name="generation_billing_records")
    op.drop_constraint("fk_generation_billing_records_funding_grant_id", "generation_billing_records", type_="foreignkey")
    op.drop_column("generation_billing_records", "billing_source_type")
    op.drop_column("generation_billing_records", "funding_grant_id")

    op.drop_index("ix_generation_funding_grants_grant_state", table_name="generation_funding_grants")
    op.drop_index("ix_generation_funding_grants_user_id", table_name="generation_funding_grants")
    op.drop_table("generation_funding_grants")

    op.drop_column("user_profiles", "monthly_free_generations_remaining")

    bind = op.get_bind()
    generation_funding_ledger_entry_kind_enum.drop(bind, checkfirst=True)
    generation_funding_grant_state_enum.drop(bind, checkfirst=True)
    generation_funding_grant_source_enum.drop(bind, checkfirst=True)
    generation_funding_grant_type_enum.drop(bind, checkfirst=True)
