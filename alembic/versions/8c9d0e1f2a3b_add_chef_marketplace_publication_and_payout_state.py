"""add chef marketplace publication and payout state

Revision ID: 8c9d0e1f2a3b
Revises: 2d4f6a8b9c0d
Create Date: 2026-04-15 08:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "8c9d0e1f2a3b"
down_revision = "2d4f6a8b9c0d"
branch_labels = None
depends_on = None


seller_payout_onboarding_status_enum = postgresql.ENUM(
    "not_started",
    "incomplete",
    "pending_review",
    "enabled",
    "restricted",
    name="sellerpayoutonboardingstatus",
    create_type=False,
)
marketplace_cookbook_publication_status_enum = postgresql.ENUM(
    "draft",
    "published",
    "unpublished",
    "archived",
    name="marketplacecookbookpublicationstatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    seller_payout_onboarding_status_enum.create(bind, checkfirst=True)
    marketplace_cookbook_publication_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "seller_payout_account_records",
        sa.Column("seller_payout_account_record_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("onboarding_status", seller_payout_onboarding_status_enum, nullable=False),
        sa.Column("charges_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("payouts_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("details_submitted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("provider_account_ref", sa.String(length=255), nullable=True),
        sa.Column("requirements_due", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status_reason", sa.String(length=300), nullable=True),
        sa.Column("provider_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_provider_sync_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("seller_payout_account_record_id"),
        sa.UniqueConstraint("user_id", name="uq_seller_payout_account_records_user_id"),
    )
    op.create_index(
        "ix_seller_payout_account_records_user_id",
        "seller_payout_account_records",
        ["user_id"],
        unique=True,
    )

    op.create_table(
        "marketplace_cookbook_publications",
        sa.Column("marketplace_cookbook_publication_id", sa.Uuid(), nullable=False),
        sa.Column("chef_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_cookbook_id", sa.Uuid(), nullable=False),
        sa.Column("publication_status", marketplace_cookbook_publication_status_enum, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("subtitle", sa.String(length=300), nullable=True),
        sa.Column("description", sa.String(length=4000), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("cover_image_url", sa.String(length=500), nullable=True),
        sa.Column("list_price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="usd"),
        sa.Column("recipe_count_snapshot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("publication_notes", sa.String(length=500), nullable=True),
        sa.Column("publication_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("unpublished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("list_price_cents >= 0", name="ck_marketplace_publications_price_non_negative"),
        sa.CheckConstraint("recipe_count_snapshot >= 0", name="ck_marketplace_publications_recipe_count_non_negative"),
        sa.ForeignKeyConstraint(["chef_user_id"], ["user_profiles.user_id"]),
        sa.ForeignKeyConstraint(["source_cookbook_id"], ["recipe_cookbooks.cookbook_id"]),
        sa.PrimaryKeyConstraint("marketplace_cookbook_publication_id"),
        sa.UniqueConstraint("chef_user_id", "source_cookbook_id", name="uq_marketplace_publications_chef_source"),
    )
    op.create_index(
        "ix_marketplace_cookbook_publications_chef_user_id",
        "marketplace_cookbook_publications",
        ["chef_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_cookbook_publications_source_cookbook_id",
        "marketplace_cookbook_publications",
        ["source_cookbook_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_cookbook_publications_slug",
        "marketplace_cookbook_publications",
        ["slug"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_marketplace_cookbook_publications_slug", table_name="marketplace_cookbook_publications")
    op.drop_index("ix_marketplace_cookbook_publications_source_cookbook_id", table_name="marketplace_cookbook_publications")
    op.drop_index("ix_marketplace_cookbook_publications_chef_user_id", table_name="marketplace_cookbook_publications")
    op.drop_table("marketplace_cookbook_publications")

    op.drop_index("ix_seller_payout_account_records_user_id", table_name="seller_payout_account_records")
    op.drop_table("seller_payout_account_records")

    bind = op.get_bind()
    marketplace_cookbook_publication_status_enum.drop(bind, checkfirst=True)
    seller_payout_onboarding_status_enum.drop(bind, checkfirst=True)
