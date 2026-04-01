"""
models/authored_recipe.py
Native authored-recipe domain models and deterministic projection helpers.

These models represent chef-authored recipes as first-class GRASP records
without reusing Session or DinnerConcept surfaces. They preserve structured
metadata the current generation/enrichment pipeline does not have native fields
for (yield guidance, storage, hold, reheat, equipment notes, authored
step-level timing windows) while compiling deterministically into the existing
scheduling-facing RawRecipe / RecipeStep seam.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field as PydanticField, field_validator, model_validator
from sqlalchemy import JSON
from sqlmodel import Column, Field, Relationship, SQLModel

from app.models.enums import Resource
from app.models.recipe import Ingredient, RawRecipe, RecipeStep


class AuthoredRecipeYield(BaseModel):
    quantity: float = PydanticField(gt=0)
    unit: str = PydanticField(min_length=1, max_length=80)
    notes: Optional[str] = None


class AuthoredRecipeDependency(BaseModel):
    step_id: str = PydanticField(min_length=1, max_length=120)
    kind: str = PydanticField(default="finish_to_start", min_length=1, max_length=40)
    lag_minutes: int = 0

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        allowed = {"finish_to_start"}
        if value not in allowed:
            raise ValueError(f"dependency kind must be one of {sorted(allowed)}, got {value!r}")
        return value


class AuthoredRecipeStep(BaseModel):
    title: str = PydanticField(min_length=1, max_length=200)
    instruction: str = PydanticField(min_length=1)
    duration_minutes: int = PydanticField(gt=0)
    duration_max: Optional[int] = PydanticField(default=None, gt=0)
    resource: Resource
    required_equipment: list[str] = PydanticField(default_factory=list)
    dependencies: list[AuthoredRecipeDependency] = PydanticField(default_factory=list)
    can_be_done_ahead: bool = False
    prep_ahead_window: Optional[str] = None
    prep_ahead_notes: Optional[str] = None
    target_internal_temperature_f: Optional[int] = PydanticField(default=None, ge=1, le=500)
    until_condition: Optional[str] = None
    yield_contribution: Optional[str] = None
    chef_notes: Optional[str] = None

    @field_validator("duration_max")
    @classmethod
    def duration_max_must_exceed_min(cls, value: Optional[int], info) -> Optional[int]:
        if value is not None:
            duration_minutes = info.data.get("duration_minutes")
            if duration_minutes is not None and value < duration_minutes:
                raise ValueError(f"duration_max ({value}) must be >= duration_minutes ({duration_minutes})")
        return value

    @field_validator("required_equipment")
    @classmethod
    def normalize_required_equipment(cls, equipment: list[str]) -> list[str]:
        normalized = [item.strip() for item in equipment if item and item.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("required_equipment must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_prep_ahead_fields(self) -> "AuthoredRecipeStep":
        if self.can_be_done_ahead and not self.prep_ahead_window:
            raise ValueError("prep_ahead_window is required when can_be_done_ahead is true")
        if not self.can_be_done_ahead and (self.prep_ahead_window or self.prep_ahead_notes):
            raise ValueError("prep_ahead_window/prep_ahead_notes require can_be_done_ahead=true")
        return self


class AuthoredRecipeStorageGuidance(BaseModel):
    method: str = PydanticField(min_length=1, max_length=120)
    duration: str = PydanticField(min_length=1, max_length=120)
    notes: Optional[str] = None


class AuthoredRecipeHoldGuidance(BaseModel):
    method: str = PydanticField(min_length=1, max_length=120)
    max_duration: str = PydanticField(min_length=1, max_length=120)
    notes: Optional[str] = None


class AuthoredRecipeReheatGuidance(BaseModel):
    method: str = PydanticField(min_length=1, max_length=120)
    target: Optional[str] = None
    notes: Optional[str] = None


class AuthoredRecipeBase(BaseModel):
    title: str = PydanticField(min_length=1, max_length=200)
    description: str = PydanticField(min_length=1)
    cuisine: str = PydanticField(min_length=1, max_length=120)
    yield_info: AuthoredRecipeYield
    ingredients: list[Ingredient] = PydanticField(default_factory=list)
    steps: list[AuthoredRecipeStep] = PydanticField(default_factory=list)
    equipment_notes: list[str] = PydanticField(default_factory=list)
    storage: Optional[AuthoredRecipeStorageGuidance] = None
    hold: Optional[AuthoredRecipeHoldGuidance] = None
    reheat: Optional[AuthoredRecipeReheatGuidance] = None
    make_ahead_guidance: Optional[str] = None
    plating_notes: Optional[str] = None
    chef_notes: Optional[str] = None

    @field_validator("equipment_notes")
    @classmethod
    def normalize_equipment_notes(cls, notes: list[str]) -> list[str]:
        return [note.strip() for note in notes if note and note.strip()]

    @model_validator(mode="after")
    def validate_step_graph(self) -> "AuthoredRecipeBase":
        if not self.ingredients:
            raise ValueError("ingredients must contain at least one item")
        if not self.steps:
            raise ValueError("steps must contain at least one item")

        step_ids = [build_authored_step_id(self.title, index) for index, _ in enumerate(self.steps, start=1)]
        step_id_set = set(step_ids)

        for index, step in enumerate(self.steps, start=1):
            own_step_id = step_ids[index - 1]
            for dependency in step.dependencies:
                if dependency.step_id not in step_id_set:
                    raise ValueError(
                        f"Step '{own_step_id}' depends on '{dependency.step_id}' which does not exist. "
                        f"Available: {sorted(step_id_set)}"
                    )
                if dependency.step_id == own_step_id:
                    raise ValueError(f"Step '{own_step_id}' cannot depend on itself")
                if dependency.lag_minutes < 0:
                    raise ValueError(f"Step '{own_step_id}' dependency '{dependency.step_id}' has negative lag_minutes")

        return self

    def compile_raw_recipe(self) -> RawRecipe:
        return RawRecipe(
            name=self.title,
            description=self.description,
            servings=max(1, int(self.yield_info.quantity)),
            cuisine=self.cuisine,
            estimated_total_minutes=sum(step.duration_minutes for step in self.steps),
            ingredients=self.ingredients,
            steps=[compile_authored_step_description(step) for step in self.steps],
        )

    def compile_recipe_steps(self) -> list[RecipeStep]:
        compiled_steps: list[RecipeStep] = []
        for index, step in enumerate(self.steps, start=1):
            step_id = build_authored_step_id(self.title, index)
            compiled_steps.append(
                RecipeStep(
                    step_id=step_id,
                    description=compile_authored_step_description(step),
                    duration_minutes=step.duration_minutes,
                    duration_max=step.duration_max,
                    depends_on=[dependency.step_id for dependency in step.dependencies],
                    resource=step.resource,
                    required_equipment=step.required_equipment,
                    can_be_done_ahead=step.can_be_done_ahead,
                    prep_ahead_window=step.prep_ahead_window,
                    prep_ahead_notes=step.prep_ahead_notes,
                )
            )
        return compiled_steps


class RecipeCookbookBase(BaseModel):
    name: str = PydanticField(min_length=1, max_length=120)
    description: Optional[str] = PydanticField(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class RecipeCookbookCreate(RecipeCookbookBase):
    pass


class RecipeCookbookRead(RecipeCookbookBase):
    cookbook_id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AuthoredRecipeCookbookSummary(BaseModel):
    cookbook_id: uuid.UUID
    name: str
    description: Optional[str] = None


class AuthoredRecipeCreate(AuthoredRecipeBase):
    user_id: uuid.UUID
    cookbook_id: Optional[uuid.UUID] = None


class AuthoredRecipeUpdateCookbook(BaseModel):
    cookbook_id: Optional[uuid.UUID] = None


class AuthoredRecipeRead(AuthoredRecipeBase):
    recipe_id: uuid.UUID
    user_id: uuid.UUID
    cookbook_id: Optional[uuid.UUID] = None
    cookbook: Optional[AuthoredRecipeCookbookSummary] = None
    created_at: datetime
    updated_at: datetime


class AuthoredRecipeListItem(BaseModel):
    recipe_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    cuisine: str
    cookbook_id: Optional[uuid.UUID] = None
    cookbook: Optional[AuthoredRecipeCookbookSummary] = None
    created_at: datetime
    updated_at: datetime


class RecipeCookbookRecord(SQLModel, table=True):
    """Persisted user-owned cookbook container for authored recipes."""

    __tablename__ = "recipe_cookbooks"

    cookbook_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    name: str = Field(index=True)
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    recipes: list["AuthoredRecipeRecord"] = Relationship(back_populates="cookbook")


class AuthoredRecipeRecord(SQLModel, table=True):
    """Persisted user-owned authored recipe record."""

    __tablename__ = "authored_recipes"

    recipe_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    cookbook_id: Optional[uuid.UUID] = Field(default=None, foreign_key="recipe_cookbooks.cookbook_id", index=True)
    title: str = Field(index=True)
    description: str
    cuisine: str
    authored_payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    cookbook: Optional[RecipeCookbookRecord] = Relationship(back_populates="recipes")


def build_authored_recipe_slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug or "recipe"


def build_authored_step_id(title: str, index: int) -> str:
    if index <= 0:
        raise ValueError(f"step index must be >= 1, got {index}")
    return f"{build_authored_recipe_slug(title)}_step_{index}"


def compile_authored_step_description(step: AuthoredRecipeStep) -> str:
    parts = [step.title.strip(), step.instruction.strip()]
    if step.until_condition:
        parts.append(f"Until: {step.until_condition.strip()}")
    if step.yield_contribution:
        parts.append(f"Yield: {step.yield_contribution.strip()}")
    if step.target_internal_temperature_f is not None:
        parts.append(f"Target temp: {step.target_internal_temperature_f}F")
    if step.chef_notes:
        parts.append(f"Chef note: {step.chef_notes.strip()}")
    return ". ".join(part for part in parts if part)
