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
    """How much the recipe produces. Used to derive servings in compile_raw_recipe()."""
    quantity: float = PydanticField(gt=0)       # e.g. 4 (servings), 500 (grams)
    unit: str = PydanticField(min_length=1, max_length=80)  # e.g. "servings", "grams"
    notes: Optional[str] = None  # e.g. "makes one 9-inch cake"


class AuthoredRecipeDependency(BaseModel):
    """Step dependency edge in an authored recipe's step graph.

    Only "finish_to_start" is supported in V1 — meaning the referenced step
    must complete before this step can begin. lag_minutes allows a delay
    between finish and start (e.g. "rest 10 minutes before slicing").
    """

    # The step_id this step depends on. Must match a step_id in the same recipe,
    # validated by AuthoredRecipeBase.validate_step_graph().
    step_id: str = PydanticField(min_length=1, max_length=120)

    # Dependency type — only "finish_to_start" supported in V1.
    # Validated explicitly to make the constraint visible in error messages.
    kind: str = PydanticField(default="finish_to_start", min_length=1, max_length=40)

    # Optional delay after predecessor finishes (e.g. 10-minute rest between steps).
    lag_minutes: int = 0

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        allowed = {"finish_to_start"}
        if value not in allowed:
            raise ValueError(f"dependency kind must be one of {sorted(allowed)}, got {value!r}")
        return value


class AuthoredRecipeStep(BaseModel):
    """One step in a chef-authored recipe. Richer than RecipeStep — preserves
    domain knowledge (internal temperatures, yield contributions, chef notes)
    that the LLM pipeline doesn't have native fields for.

    compile() → RecipeStep drops the chef-specific fields and produces the
    scheduling-facing model that the DAG builder consumes.
    """

    title: str = PydanticField(min_length=1, max_length=200)      # e.g. "Sear the duck breast"
    instruction: str = PydanticField(min_length=1)                  # full technique description
    duration_minutes: int = PydanticField(gt=0)
    duration_max: Optional[int] = PydanticField(default=None, gt=0)  # None = deterministic

    # Which kitchen resource this step uses — maps directly to RecipeStep.resource.
    resource: Resource

    # Equipment needed — e.g. ["cast_iron_skillet"]. Capacity=1 per item;
    # steps requiring the same equipment cannot overlap in the scheduler.
    required_equipment: list[str] = PydanticField(default_factory=list)

    # Step dependencies within this recipe (not cross-recipe).
    # step_id references are validated against build_authored_step_id() outputs
    # by AuthoredRecipeBase.validate_step_graph().
    dependencies: list[AuthoredRecipeDependency] = PydanticField(default_factory=list)

    can_be_done_ahead: bool = False
    prep_ahead_window: Optional[str] = None  # required when can_be_done_ahead=True
    prep_ahead_notes: Optional[str] = None

    # Chef-specific metadata compiled into the step description text.
    # These are NOT separate fields in RecipeStep — they get baked into
    # compile_authored_step_description() so the enricher sees them as context.
    target_internal_temperature_f: Optional[int] = PydanticField(default=None, ge=1, le=500)
    until_condition: Optional[str] = None     # e.g. "golden brown and crisp"
    yield_contribution: Optional[str] = None  # e.g. "yields 2 cups fond"
    chef_notes: Optional[str] = None          # practical tips for execution

    @field_validator("duration_max")
    @classmethod
    def duration_max_must_exceed_min(cls, value: Optional[int], info) -> Optional[int]:
        # Mirror of RecipeStep's validator — keeps the constraint visible at
        # the authored layer before it reaches the pipeline.
        if value is not None:
            duration_minutes = info.data.get("duration_minutes")
            if duration_minutes is not None and value < duration_minutes:
                raise ValueError(f"duration_max ({value}) must be >= duration_minutes ({duration_minutes})")
        return value

    @field_validator("required_equipment")
    @classmethod
    def normalize_required_equipment(cls, equipment: list[str]) -> list[str]:
        # Strip whitespace and reject duplicates — duplicates would double-count
        # equipment capacity in the scheduler's equipment_utilisation tracking.
        normalized = [item.strip() for item in equipment if item and item.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("required_equipment must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_prep_ahead_fields(self) -> "AuthoredRecipeStep":
        # Enforce the prep-ahead contract: window is required when can_be_done_ahead=True,
        # and window/notes are forbidden when False. Mirrors enricher prompt rules.
        if self.can_be_done_ahead and not self.prep_ahead_window:
            raise ValueError("prep_ahead_window is required when can_be_done_ahead is true")
        if not self.can_be_done_ahead and (self.prep_ahead_window or self.prep_ahead_notes):
            raise ValueError("prep_ahead_window/prep_ahead_notes require can_be_done_ahead=true")
        return self


class AuthoredRecipeStorageGuidance(BaseModel):
    """How to store the finished dish. Compiled into the step description for RAG context."""
    method: str = PydanticField(min_length=1, max_length=120)    # e.g. "refrigerate in airtight container"
    duration: str = PydanticField(min_length=1, max_length=120)  # e.g. "up to 3 days"
    notes: Optional[str] = None


class AuthoredRecipeHoldGuidance(BaseModel):
    """How to hold the dish before service (e.g. in a bain-marie at 140°F)."""
    method: str = PydanticField(min_length=1, max_length=120)
    max_duration: str = PydanticField(min_length=1, max_length=120)
    notes: Optional[str] = None


class AuthoredRecipeReheatGuidance(BaseModel):
    """Reheating instructions for make-ahead execution."""
    method: str = PydanticField(min_length=1, max_length=120)
    target: Optional[str] = None  # e.g. "165°F internal temperature"
    notes: Optional[str] = None


class AuthoredRecipeBase(BaseModel):
    """Shared authored recipe fields used by both Create and Read models.

    compile_raw_recipe() and compile_recipe_steps() are the deterministic
    projection seams that convert authored-recipe domain objects into the
    pipeline-facing RawRecipe / RecipeStep models without calling Claude.
    """

    title: str = PydanticField(min_length=1, max_length=200)
    description: str = PydanticField(min_length=1)
    cuisine: str = PydanticField(min_length=1, max_length=120)
    yield_info: AuthoredRecipeYield  # used to derive servings in compile_raw_recipe()
    ingredients: list[Ingredient] = PydanticField(default_factory=list)
    steps: list[AuthoredRecipeStep] = PydanticField(default_factory=list)
    equipment_notes: list[str] = PydanticField(default_factory=list)  # general equipment guidance

    # Optional service guidance fields — compiled into context for the pipeline
    storage: Optional[AuthoredRecipeStorageGuidance] = None
    hold: Optional[AuthoredRecipeHoldGuidance] = None
    reheat: Optional[AuthoredRecipeReheatGuidance] = None
    make_ahead_guidance: Optional[str] = None  # general note about what can be done in advance
    plating_notes: Optional[str] = None        # presentation guidance
    chef_notes: Optional[str] = None           # overall practical advice

    @field_validator("equipment_notes")
    @classmethod
    def normalize_equipment_notes(cls, notes: list[str]) -> list[str]:
        # Strip empty strings and whitespace-only entries so downstream code
        # can rely on all notes being non-empty.
        return [note.strip() for note in notes if note and note.strip()]

    @model_validator(mode="after")
    def validate_step_graph(self) -> "AuthoredRecipeBase":
        """Validate ingredients presence and dependency graph integrity.

        Runs mode='after' so step_ids can be derived from the full steps list.
        Generates step IDs using build_authored_step_id() to match what the
        pipeline will use — if this validator passes, the DAG builder won't
        encounter dangling dependency references.
        """
        if not self.ingredients:
            raise ValueError("ingredients must contain at least one item")
        if not self.steps:
            raise ValueError("steps must contain at least one item")

        # Generate the canonical step IDs for all steps (1-indexed).
        # These must match what build_authored_step_id() produces so dependency
        # references in step.dependencies can be validated against them.
        step_ids = [build_authored_step_id(self.title, index) for index, _ in enumerate(self.steps, start=1)]
        step_id_set = set(step_ids)

        for index, step in enumerate(self.steps, start=1):
            own_step_id = step_ids[index - 1]
            for dependency in step.dependencies:
                # Dangling reference — would cause KeyError in DAG builder
                if dependency.step_id not in step_id_set:
                    raise ValueError(
                        f"Step '{own_step_id}' depends on '{dependency.step_id}' which does not exist. "
                        f"Available: {sorted(step_id_set)}"
                    )
                # Self-dependency — would create a cycle of length 1 in NetworkX
                if dependency.step_id == own_step_id:
                    raise ValueError(f"Step '{own_step_id}' cannot depend on itself")
                # Negative lag is physically impossible
                if dependency.lag_minutes < 0:
                    raise ValueError(f"Step '{own_step_id}' dependency '{dependency.step_id}' has negative lag_minutes")

        return self

    def compile_raw_recipe(self) -> RawRecipe:
        """Convert to RawRecipe for the generator seam (skips LLM generation entirely).

        When concept_source is "authored", the generator node calls this method
        instead of calling Claude. The compiled RawRecipe flows through enricher
        and validator exactly like a generated recipe — only the origin differs.
        """
        return RawRecipe(
            name=self.title,
            description=self.description,
            # yield_info.quantity may be fractional (e.g. 4.5 portions) — take the
            # integer floor for servings, minimum 1 to prevent validation errors.
            servings=max(1, int(self.yield_info.quantity)),
            cuisine=self.cuisine,
            # Sum all step durations as a rough total — enricher uses this to
            # calibrate step-level durations in the enrichment prompt.
            estimated_total_minutes=sum(step.duration_minutes for step in self.steps),
            ingredients=self.ingredients,
            # Each step is compiled to a flat description string that the enricher
            # can parse. compile_authored_step_description() bakes in temperatures,
            # conditions, yield contributions, and chef notes.
            steps=[compile_authored_step_description(step) for step in self.steps],
        )

    def compile_recipe_steps(self) -> list[RecipeStep]:
        """Convert steps to RecipeStep objects, bypassing the enricher's LLM call.

        Used for authored sessions where the chef has already provided precise
        timing, resource assignments, and dependency edges — no LLM enrichment needed.
        Dependencies are translated from AuthoredRecipeDependency.step_id references
        to the canonical step_id format from build_authored_step_id().
        """
        compiled_steps: list[RecipeStep] = []
        for index, step in enumerate(self.steps, start=1):
            step_id = build_authored_step_id(self.title, index)
            compiled_steps.append(
                RecipeStep(
                    step_id=step_id,
                    description=compile_authored_step_description(step),
                    duration_minutes=step.duration_minutes,
                    duration_max=step.duration_max,
                    # Translate AuthoredRecipeDependency.step_id → pipeline step_ids
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
    """Shared fields for cookbook container models (Create/Read)."""

    name: str = PydanticField(min_length=1, max_length=120)
    description: Optional[str] = PydanticField(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        # Strip whitespace — prevents invisible-character bugs in cookbook name lookups
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
        # Treat whitespace-only descriptions as absent rather than storing empty strings
        return normalized or None


class RecipeCookbookCreate(RecipeCookbookBase):
    """Request body for POST /recipe-cookbooks."""
    pass


class RecipeCookbookRead(RecipeCookbookBase):
    """Response model for cookbook list/detail endpoints."""
    cookbook_id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AuthoredRecipeCookbookSummary(BaseModel):
    """Minimal cookbook reference embedded in AuthoredRecipeRead responses.
    Avoids loading the full RecipeCookbookRead when only the name is needed."""
    cookbook_id: uuid.UUID
    name: str
    description: Optional[str] = None


class AuthoredRecipeCreate(AuthoredRecipeBase):
    """Request body for POST /authored-recipes.
    user_id and cookbook_id come from auth context / request path, not the body.
    They are removed from the authored_payload JSON before persistence to avoid
    duplication between the JSON blob and the SQLModel row columns."""
    user_id: uuid.UUID
    cookbook_id: Optional[uuid.UUID] = None


class AuthoredRecipeUpdateCookbook(BaseModel):
    """Request body for PATCH /authored-recipes/{id}/cookbook.
    None = remove from any cookbook; UUID = move to that cookbook."""
    cookbook_id: Optional[uuid.UUID] = None


class AuthoredRecipeRead(AuthoredRecipeBase):
    """Full authored recipe response with DB metadata and cookbook summary."""
    recipe_id: uuid.UUID
    user_id: uuid.UUID
    cookbook_id: Optional[uuid.UUID] = None
    cookbook: Optional[AuthoredRecipeCookbookSummary] = None
    created_at: datetime
    updated_at: datetime


class AuthoredRecipeListItem(BaseModel):
    """Lightweight recipe summary for list endpoints (avoids loading full authored_payload)."""
    recipe_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    cuisine: str
    cookbook_id: Optional[uuid.UUID] = None
    cookbook: Optional[AuthoredRecipeCookbookSummary] = None
    created_at: datetime
    updated_at: datetime


class RecipeCookbookRecord(SQLModel, table=True):
    """Persisted user-owned cookbook container for authored recipes.

    A cookbook is a named collection (e.g. "My Winter Menu") that groups
    authored recipes for organization and for use as a planner_cookbook_target.
    """

    __tablename__ = "recipe_cookbooks"

    cookbook_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    name: str = Field(index=True)  # indexed for title-search resolution in planner flow
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    # ORM relationship — lazy-loaded when building AuthoredRecipeRead responses
    recipes: list["AuthoredRecipeRecord"] = Relationship(back_populates="cookbook")


class AuthoredRecipeRecord(SQLModel, table=True):
    """Persisted user-owned authored recipe record.

    authored_payload stores the full AuthoredRecipeCreate JSON blob (minus
    user_id and cookbook_id which live in dedicated columns). On read, the
    route reconstructs AuthoredRecipeRead by merging the payload with row metadata.
    This avoids a wide, sparse table while keeping all structured data queryable via Postgres JSON ops.
    """

    __tablename__ = "authored_recipes"

    recipe_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    cookbook_id: Optional[uuid.UUID] = Field(default=None, foreign_key="recipe_cookbooks.cookbook_id", index=True)

    # Denormalized columns for fast list queries — avoids deserializing authored_payload
    # just to display title and cuisine in the recipe list UI.
    title: str = Field(index=True)
    description: str
    cuisine: str

    # Full AuthoredRecipeBase JSON. user_id/cookbook_id stripped before storage.
    # Deserialized via AuthoredRecipeRead.model_validate({**payload, **row_fields}).
    authored_payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    # ORM back-reference to the parent cookbook (None if not in a cookbook)
    cookbook: Optional[RecipeCookbookRecord] = Relationship(back_populates="recipes")


def build_authored_recipe_slug(title: str) -> str:
    """Convert recipe title to URL-safe slug for step_id prefix generation.
    'Duck Confit' → 'duck_confit'. Fallback to 'recipe' for degenerate titles."""
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug or "recipe"


def build_authored_step_id(title: str, index: int) -> str:
    """Generate the canonical step_id for a given step position.
    Format: {recipe_slug}_step_{index} — mirrors the enricher's convention.
    Used both for step_id generation and for dependency reference validation.
    """
    if index <= 0:
        raise ValueError(f"step index must be >= 1, got {index}")
    return f"{build_authored_recipe_slug(title)}_step_{index}"


def compile_authored_step_description(step: AuthoredRecipeStep) -> str:
    """Flatten an AuthoredRecipeStep into a plain-text description string.

    The pipeline (enricher → validator → DAG builder) only sees flat strings —
    it doesn't know about AuthoredRecipeStep fields like until_condition or
    target_internal_temperature_f. This function bakes those details into
    the description text so the enricher's LLM can see them as context.

    Output format: "Title. Instruction. Until: X. Yield: Y. Target temp: ZF. Chef note: W."
    """
    parts = [step.title.strip(), step.instruction.strip()]
    if step.until_condition:
        parts.append(f"Until: {step.until_condition.strip()}")
    if step.yield_contribution:
        parts.append(f"Yield: {step.yield_contribution.strip()}")
    if step.target_internal_temperature_f is not None:
        parts.append(f"Target temp: {step.target_internal_temperature_f}F")
    if step.chef_notes:
        parts.append(f"Chef note: {step.chef_notes.strip()}")
    # Join non-empty parts with ". " — empty parts were already filtered above
    return ". ".join(part for part in parts if part)
