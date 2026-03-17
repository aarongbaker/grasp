"""
models/user.py
UserProfile, KitchenConfig, Equipment — SQLModel → Postgres.

Pure SQLModel (not pure Pydantic) because these persist to Postgres.
Pipeline models (RawRecipe, GRASPState, etc.) are pure Pydantic —
they never touch the DB directly; LangGraph's checkpointer handles them.

KitchenConfig is snapshotted into GRASPState at session start so a
config change mid-run cannot corrupt an in-progress schedule.
"""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON
from sqlmodel import Column, Field, Relationship, SQLModel

from models.enums import EquipmentCategory


class Equipment(SQLModel, table=True):
    __tablename__ = "equipment"

    equipment_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    name: str
    category: EquipmentCategory
    # Each piece unlocks techniques the LLM generator can suggest.
    # sous vide → precise-temperature cooking; stand mixer → laminated doughs.
    unlocks_techniques: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Relationship back to UserProfile
    user: Optional["UserProfile"] = Relationship(back_populates="equipment")


class KitchenConfig(SQLModel, table=True):
    __tablename__ = "kitchen_configs"

    kitchen_config_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    max_burners: int = Field(default=4)
    max_oven_racks: int = Field(default=2)
    has_second_oven: bool = Field(default=False)

    user: Optional["UserProfile"] = Relationship(back_populates="kitchen_config")


class UserProfile(SQLModel, table=True):
    __tablename__ = "user_profiles"

    user_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    email: str = Field(unique=True, index=True)
    kitchen_config_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="kitchen_configs.kitchen_config_id"
    )
    # Merged into every DinnerConcept at session creation
    dietary_defaults: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    kitchen_config: Optional[KitchenConfig] = Relationship(back_populates="user")
    equipment: list[Equipment] = Relationship(back_populates="user")
