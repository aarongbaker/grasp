"""
models/user.py
UserProfile, KitchenConfig, Equipment — SQLModel → Postgres.

Pure SQLModel (not pure Pydantic) because these persist to Postgres.
Pipeline models (RawRecipe, GRASPState, etc.) are pure Pydantic —
they never touch the DB directly; LangGraph's checkpointer handles them.

KitchenConfig is snapshotted into GRASPState at session start so a
config change mid-run cannot corrupt an in-progress schedule.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from pydantic import field_validator, model_validator
from sqlalchemy import JSON
from sqlmodel import Column, Field, Relationship, SQLModel

from app.models.enums import EquipmentCategory


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


class BurnerDescriptor(SQLModel):
    """Stable burner metadata carried in kitchen_config snapshots and scheduling output."""

    burner_id: str
    position: Optional[str] = None
    size: Optional[str] = None
    label: Optional[str] = None

    @field_validator("burner_id")
    @classmethod
    def _validate_burner_id(cls, value: str) -> str:
        burner_id = value.strip()
        if not burner_id:
            raise ValueError("burner_id must not be empty")
        return burner_id


class KitchenConfig(SQLModel, table=True):
    __tablename__ = "kitchen_configs"

    kitchen_config_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    max_burners: int = Field(default=4, ge=1, le=10)
    max_oven_racks: int = Field(default=2, ge=1, le=6)
    has_second_oven: bool = Field(default=False)
    max_second_oven_racks: int = Field(default=2, ge=1, le=6)
    # Optional ordered explicit burner descriptors. When absent, max_burners still
    # defines a fungible fallback pool and later scheduling code synthesizes burner_1..N.
    burners: list[BurnerDescriptor] = Field(default_factory=list, sa_column=Column(JSON))

    user: Optional["UserProfile"] = Relationship(back_populates="kitchen_config")

    @model_validator(mode="after")
    def _validate_burner_count(self):
        if len(self.burners) > self.max_burners:
            raise ValueError("burners count cannot exceed max_burners")
        return self


class UserProfile(SQLModel, table=True):
    __tablename__ = "user_profiles"

    @staticmethod
    def build_rag_owner_key(email: str) -> str:
        """Stable, environment-portable identity for Pinecone ownership filtering."""
        normalized = email.strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return f"email:{slug}"

    user_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    email: str = Field(unique=True, index=True)
    rag_owner_key: str = Field(index=True)
    password_hash: str = Field(default="")
    kitchen_config_id: Optional[uuid.UUID] = Field(default=None, foreign_key="kitchen_configs.kitchen_config_id")
    # Merged into every DinnerConcept at session creation
    dietary_defaults: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    kitchen_config: Optional[KitchenConfig] = Relationship(back_populates="user")
    equipment: list[Equipment] = Relationship(back_populates="user")
