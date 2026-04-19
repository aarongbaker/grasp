"""add catalog cookbook purchase ownership tables

Revision ID: 2d4f6a8b9c0d
Revises: 1776217588_generation_billing_ledger
Create Date: 2026-04-14 22:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "2d4f6a8b9c0d"
down_revision = "1776217588_generation_billing_ledger"
branch_labels = None
depends_on = None


catalog_purchase_state_enum = postgresql.ENUM(
    "pending",
    "completed",
    "cancelled",
    "failed",
    name="catalogpurchasestate",
    create_type=False,
)
catalog_purchase_provider_enum = postgresql.ENUM(
    "app",
    "stripe",
    name="catalogpurchaseprovider",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    catalog_purchase_state_enum.create(bind, checkfirst=True)
    catalog_purchase_provider_enum.create(bind, checkfirst=True)

    op.create_table(
        "catalog_cookbook_purchase_records",
        sa.Column("catalog_cookbook_purchase_record_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_cookbook_id", sa.Uuid(), nullable=False),
        sa.Column("provider", catalog_purchase_provider_enum, nullable=False),
        sa.Column("provider_checkout_ref", sa.String(length=255), nullable=True),
        sa.Column("provider_completion_ref", sa.String(length=255), nullable=True),
        sa.Column("purchase_state", catalog_purchase_state_enum, nullable=False),
        sa.Column("access_reason", sa.String(length=200), nullable=False),
        sa.Column("purchase_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("catalog_cookbook_purchase_record_id"),
        sa.UniqueConstraint("provider_completion_ref", name="uq_catalog_purchase_records_completion_ref"),
    )
    op.create_index(
        "ix_catalog_cookbook_purchase_records_user_id",
        "catalog_cookbook_purchase_records",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_cookbook_purchase_records_catalog_cookbook_id",
        "catalog_cookbook_purchase_records",
        ["catalog_cookbook_id"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_cookbook_purchase_records_provider_checkout_ref",
        "catalog_cookbook_purchase_records",
        ["provider_checkout_ref"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_cookbook_purchase_records_provider_completion_ref",
        "catalog_cookbook_purchase_records",
        ["provider_completion_ref"],
        unique=True,
    )

    op.create_table(
        "catalog_cookbook_ownership_records",
        sa.Column("catalog_cookbook_ownership_record_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_cookbook_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_record_id", sa.Uuid(), nullable=False),
        sa.Column("ownership_source", sa.String(length=100), nullable=False),
        sa.Column("access_reason", sa.String(length=200), nullable=False),
        sa.Column("ownership_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["purchase_record_id"], ["catalog_cookbook_purchase_records.catalog_cookbook_purchase_record_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("catalog_cookbook_ownership_record_id"),
        sa.UniqueConstraint("purchase_record_id", name="uq_catalog_cookbook_ownership_purchase_record_id"),
        sa.UniqueConstraint("user_id", "catalog_cookbook_id", name="uq_catalog_cookbook_ownership_user_catalog"),
    )
    op.create_index(
        "ix_catalog_cookbook_ownership_records_user_id",
        "catalog_cookbook_ownership_records",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_cookbook_ownership_records_catalog_cookbook_id",
        "catalog_cookbook_ownership_records",
        ["catalog_cookbook_id"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_cookbook_ownership_records_purchase_record_id",
        "catalog_cookbook_ownership_records",
        ["purchase_record_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_cookbook_ownership_records_purchase_record_id", table_name="catalog_cookbook_ownership_records")
    op.drop_index("ix_catalog_cookbook_ownership_records_catalog_cookbook_id", table_name="catalog_cookbook_ownership_records")
    op.drop_index("ix_catalog_cookbook_ownership_records_user_id", table_name="catalog_cookbook_ownership_records")
    op.drop_table("catalog_cookbook_ownership_records")

    op.drop_index("ix_catalog_cookbook_purchase_records_provider_completion_ref", table_name="catalog_cookbook_purchase_records")
    op.drop_index("ix_catalog_cookbook_purchase_records_provider_checkout_ref", table_name="catalog_cookbook_purchase_records")
    op.drop_index("ix_catalog_cookbook_purchase_records_catalog_cookbook_id", table_name="catalog_cookbook_purchase_records")
    op.drop_index("ix_catalog_cookbook_purchase_records_user_id", table_name="catalog_cookbook_purchase_records")
    op.drop_table("catalog_cookbook_purchase_records")

    bind = op.get_bind()
    catalog_purchase_provider_enum.drop(bind, checkfirst=True)
    catalog_purchase_state_enum.drop(bind, checkfirst=True)
