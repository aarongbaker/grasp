"""add subscription access tables

Revision ID: 9b1d7e6c4a2f
Revises: 0f2c360d63c6
Create Date: 2026-04-12 21:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "9b1d7e6c4a2f"
down_revision = "2a4b6c8d0e1f"
branch_labels = None
depends_on = None


subscription_status_enum = postgresql.ENUM(
    "active",
    "trialing",
    "past_due",
    "cancelled",
    "expired",
    "grace_period",
    name="subscriptionstatus",
    create_type=False,
)
subscription_sync_state_enum = postgresql.ENUM(
    "pending",
    "synced",
    "failed",
    name="subscriptionsyncstate",
    create_type=False,
)
entitlement_kind_enum = postgresql.ENUM(
    "catalog_preview",
    "catalog_premium",
    name="entitlementkind",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    subscription_status_enum.create(bind, checkfirst=True)
    subscription_sync_state_enum.create(bind, checkfirst=True)
    entitlement_kind_enum.create(bind, checkfirst=True)

    op.create_table(
        "subscription_snapshots",
        sa.Column("subscription_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_customer_ref", sa.String(length=255), nullable=True),
        sa.Column("provider_subscription_ref", sa.String(length=255), nullable=True),
        sa.Column("plan_code", sa.String(length=100), nullable=True),
        sa.Column("status", subscription_status_enum, nullable=False),
        sa.Column("sync_state", subscription_sync_state_enum, nullable=False),
        sa.Column("current_period_ends_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("sync_error_code", sa.String(length=100), nullable=True),
        sa.Column("sync_error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("subscription_snapshot_id"),
    )
    op.create_index("ix_subscription_snapshots_user_id", "subscription_snapshots", ["user_id"], unique=False)

    op.create_table(
        "user_entitlement_grants",
        sa.Column("entitlement_grant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", entitlement_kind_enum, nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("entitlement_grant_id"),
    )
    op.create_index("ix_user_entitlement_grants_user_id", "user_entitlement_grants", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_entitlement_grants_user_id", table_name="user_entitlement_grants")
    op.drop_table("user_entitlement_grants")
    op.drop_index("ix_subscription_snapshots_user_id", table_name="subscription_snapshots")
    op.drop_table("subscription_snapshots")

    bind = op.get_bind()
    entitlement_kind_enum.drop(bind, checkfirst=True)
    subscription_sync_state_enum.drop(bind, checkfirst=True)
    subscription_status_enum.drop(bind, checkfirst=True)
