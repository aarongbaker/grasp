"""initial schema

Revision ID: cbe7b760aae2
Revises:
Create Date: 2026-03-16 23:11:18.725075

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cbe7b760aae2"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all initial tables."""

    # ── kitchen_configs (referenced by user_profiles) ─────────────────────
    op.create_table(
        "kitchen_configs",
        sa.Column("kitchen_config_id", sa.Uuid(), primary_key=True),
        sa.Column("max_burners", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("max_oven_racks", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("has_second_oven", sa.Boolean(), nullable=False, server_default="false"),
    )

    # ── user_profiles ─────────────────────────────────────────────────────
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column(
            "kitchen_config_id",
            sa.Uuid(),
            sa.ForeignKey("kitchen_configs.kitchen_config_id"),
            nullable=True,
        ),
        sa.Column("dietary_defaults", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_user_profiles_email", "user_profiles", ["email"], unique=True)

    # ── equipment ─────────────────────────────────────────────────────────
    op.create_table(
        "equipment",
        sa.Column("equipment_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.user_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("unlocks_techniques", sa.JSON(), nullable=True),
    )
    op.create_index("ix_equipment_user_id", "equipment", ["user_id"])

    # ── sessions ──────────────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.user_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("concept_json", sa.JSON(), nullable=True),
        sa.Column("schedule_summary", sa.String(), nullable=True),
        sa.Column("total_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("error_summary", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_status", "sessions", ["status"])

    # ── book_records ──────────────────────────────────────────────────────
    op.create_table(
        "book_records",
        sa.Column("book_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.user_id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("author", sa.String(), nullable=False, server_default=""),
        sa.Column("document_type", sa.String(), nullable=True),
        sa.Column("total_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_book_records_user_id", "book_records", ["user_id"])

    # ── page_cache ────────────────────────────────────────────────────────
    op.create_table(
        "page_cache",
        sa.Column("page_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "book_id",
            sa.Uuid(),
            sa.ForeignKey("book_records.book_id"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_text", sa.Text(), nullable=False),
        sa.Column("page_hash", sa.String(), nullable=False),
        sa.Column("vision_confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("resolution_dpi", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_page_cache_book_id", "page_cache", ["book_id"])

    # ── cookbook_chunks ────────────────────────────────────────────────────
    op.create_table(
        "cookbook_chunks",
        sa.Column("chunk_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "book_id",
            sa.Uuid(),
            sa.ForeignKey("book_records.book_id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.user_id"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("chunk_type", sa.String(), nullable=False),
        sa.Column("chapter", sa.String(), nullable=False, server_default=""),
        sa.Column("page_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pinecone_upserted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cookbook_chunks_book_id", "cookbook_chunks", ["book_id"])
    op.create_index("ix_cookbook_chunks_user_id", "cookbook_chunks", ["user_id"])

    # ── ingestion_jobs ────────────────────────────────────────────────────
    op.create_table(
        "ingestion_jobs",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.user_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("book_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("book_statuses", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ingestion_jobs_user_id", "ingestion_jobs", ["user_id"])


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    op.drop_table("ingestion_jobs")
    op.drop_table("cookbook_chunks")
    op.drop_table("page_cache")
    op.drop_table("book_records")
    op.drop_table("sessions")
    op.drop_table("equipment")
    op.drop_table("user_profiles")
    op.drop_table("kitchen_configs")
