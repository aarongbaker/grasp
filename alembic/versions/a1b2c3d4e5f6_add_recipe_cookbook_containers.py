"""add recipe cookbook containers and authored recipe assignment

Revision ID: a1b2c3d4e5f6
Revises: f7a8b9c0d1e2
Create Date: 2026-04-01 14:45:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recipe_cookbooks",
        sa.Column("cookbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"]),
        sa.PrimaryKeyConstraint("cookbook_id"),
    )
    op.create_index(op.f("ix_recipe_cookbooks_name"), "recipe_cookbooks", ["name"], unique=False)
    op.create_index(op.f("ix_recipe_cookbooks_user_id"), "recipe_cookbooks", ["user_id"], unique=False)

    op.add_column("authored_recipes", sa.Column("cookbook_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_authored_recipes_cookbook_id_recipe_cookbooks",
        "authored_recipes",
        "recipe_cookbooks",
        ["cookbook_id"],
        ["cookbook_id"],
    )
    op.create_index(op.f("ix_authored_recipes_cookbook_id"), "authored_recipes", ["cookbook_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_authored_recipes_cookbook_id"), table_name="authored_recipes")
    op.drop_constraint("fk_authored_recipes_cookbook_id_recipe_cookbooks", "authored_recipes", type_="foreignkey")
    op.drop_column("authored_recipes", "cookbook_id")

    op.drop_index(op.f("ix_recipe_cookbooks_user_id"), table_name="recipe_cookbooks")
    op.drop_index(op.f("ix_recipe_cookbooks_name"), table_name="recipe_cookbooks")
    op.drop_table("recipe_cookbooks")
