"""
models/pipeline.py
GRASPState and DinnerConcept — the central pipeline objects.

CRITICAL LangGraph architecture decision:
GRASPState is a TypedDict (not a Pydantic model) because LangGraph requires
TypedDict for its state schema. The `errors` field uses Annotated with
operator.add as the reducer — this tells LangGraph to ACCUMULATE errors
across nodes rather than replace them. Every other field uses REPLACE semantics.

Node idempotency contract (§2.10): nodes return partial dicts that replace
their specific fields. Never append to raw_recipes, enriched_recipes, etc.
Replace the entire list. This makes every node safe to re-run on checkpoint
resume without producing duplicate data.

DinnerConcept fields: dietary_restrictions merged from UserProfile.dietary_defaults
at session creation. The chef doesn't have to re-specify their restrictions
every session.
"""

import operator
import re
import uuid
from enum import Enum
from typing import Annotated, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.models.enums import ErrorType, MealType, Occasion
from app.models.scheduling import OneOvenConflictSummary


class PlannerLibraryCookbookPlanningMode(str, Enum):
    """Planner intent for how tightly cookbook scope should constrain generation."""

    STRICT = "strict"
    COOKBOOK_BIASED = "cookbook_biased"


class SelectedCookbookRecipe(BaseModel):
    """Authoritative cookbook recipe reference captured at session creation."""

    chunk_id: uuid.UUID
    book_id: uuid.UUID
    book_title: str = Field(max_length=500)
    text: str = Field(min_length=1)
    chapter: str = ""
    page_number: int = Field(ge=0, default=0)


class SelectedAuthoredRecipe(BaseModel):
    """Minimal authored recipe selection persisted into the session concept."""

    recipe_id: uuid.UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PlannerLibraryAuthoredRecipeAnchor(BaseModel):
    """Planner-lane reference to one owned authored recipe in the private library."""

    recipe_id: uuid.UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PlannerLibraryCookbookTarget(BaseModel):
    """Planner-lane reference to one owned authored-recipe cookbook container."""

    cookbook_id: uuid.UUID
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: Optional[str] = Field(default=None, max_length=500)
    mode: PlannerLibraryCookbookPlanningMode


class PlannerReferenceKind(str, Enum):
    AUTHORED = "authored"
    COOKBOOK = "cookbook"


class PlannerResolutionMatchStatus(str, Enum):
    NO_MATCH = "no_match"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"


class PlannerReferenceResolutionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    kind: PlannerReferenceKind
    reference: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PlannerAuthoredResolutionMatch(BaseModel):
    kind: Literal[PlannerReferenceKind.AUTHORED] = PlannerReferenceKind.AUTHORED
    recipe_id: uuid.UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PlannerCookbookResolutionMatch(BaseModel):
    kind: Literal[PlannerReferenceKind.COOKBOOK] = PlannerReferenceKind.COOKBOOK
    cookbook_id: uuid.UUID
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: Optional[str] = Field(default=None, max_length=500)


PlannerResolutionMatch = Annotated[
    PlannerAuthoredResolutionMatch | PlannerCookbookResolutionMatch,
    Field(discriminator="kind"),
]


class PlannerReferenceResolutionResponse(BaseModel):
    kind: PlannerReferenceKind
    reference: str
    status: PlannerResolutionMatchStatus
    matches: list[PlannerResolutionMatch] = Field(default_factory=list)


class DinnerConcept(BaseModel):
    """
    Hybrid input: free_text preserves nuance; typed fields ensure
    safety-critical constraints (dietary_restrictions, meal_type) are
    never ambiguous. guest_count bounded [1, 100].
    """

    free_text: str = Field(max_length=2000)
    guest_count: int = Field(ge=1, le=100)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None  # "HH:MM" 24-hour format, e.g. "19:00"
    concept_source: Literal[
        "free_text", "cookbook", "authored", "planner_authored_anchor", "planner_cookbook_target"
    ] = "free_text"
    selected_recipes: list[SelectedCookbookRecipe] = []
    selected_authored_recipe: Optional[SelectedAuthoredRecipe] = None
    planner_authored_recipe_anchor: Optional[PlannerLibraryAuthoredRecipeAnchor] = None
    planner_cookbook_target: Optional[PlannerLibraryCookbookTarget] = None

    @field_validator("serving_time")
    @classmethod
    def validate_serving_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("serving_time must be in HH:MM 24-hour format (e.g. '19:00')")
        return v

    @model_validator(mode="after")
    def validate_source_contract(self) -> "DinnerConcept":
        has_selected_recipes = bool(self.selected_recipes)
        has_selected_authored_recipe = self.selected_authored_recipe is not None
        has_planner_authored_anchor = self.planner_authored_recipe_anchor is not None
        has_planner_cookbook_target = self.planner_cookbook_target is not None

        if self.concept_source == "cookbook":
            if not has_selected_recipes:
                raise ValueError("selected_recipes is required when concept_source is 'cookbook'")
            if has_selected_authored_recipe:
                raise ValueError(
                    "selected_authored_recipe is only allowed when concept_source is 'authored'"
                )
            if has_planner_authored_anchor:
                raise ValueError(
                    "planner_authored_recipe_anchor is only allowed when concept_source is 'planner_authored_anchor'"
                )
            if has_planner_cookbook_target:
                raise ValueError(
                    "planner_cookbook_target is only allowed when concept_source is 'planner_cookbook_target'"
                )
        elif self.concept_source == "authored":
            if has_selected_recipes:
                raise ValueError("selected_recipes is only allowed when concept_source is 'cookbook'")
            if not has_selected_authored_recipe:
                raise ValueError(
                    "selected_authored_recipe is required when concept_source is 'authored'"
                )
            if has_planner_authored_anchor:
                raise ValueError(
                    "planner_authored_recipe_anchor is only allowed when concept_source is 'planner_authored_anchor'"
                )
            if has_planner_cookbook_target:
                raise ValueError(
                    "planner_cookbook_target is only allowed when concept_source is 'planner_cookbook_target'"
                )
        elif self.concept_source == "planner_authored_anchor":
            if has_selected_recipes:
                raise ValueError("selected_recipes is only allowed when concept_source is 'cookbook'")
            if has_selected_authored_recipe:
                raise ValueError(
                    "selected_authored_recipe is only allowed when concept_source is 'authored'"
                )
            if not has_planner_authored_anchor:
                raise ValueError(
                    "planner_authored_recipe_anchor is required when concept_source is 'planner_authored_anchor'"
                )
            if has_planner_cookbook_target:
                raise ValueError(
                    "planner_cookbook_target is only allowed when concept_source is 'planner_cookbook_target'"
                )
        elif self.concept_source == "planner_cookbook_target":
            if has_selected_recipes:
                raise ValueError("selected_recipes is only allowed when concept_source is 'cookbook'")
            if has_selected_authored_recipe:
                raise ValueError(
                    "selected_authored_recipe is only allowed when concept_source is 'authored'"
                )
            if has_planner_authored_anchor:
                raise ValueError(
                    "planner_authored_recipe_anchor is only allowed when concept_source is 'planner_authored_anchor'"
                )
            if not has_planner_cookbook_target:
                raise ValueError(
                    "planner_cookbook_target is required when concept_source is 'planner_cookbook_target'"
                )
        else:
            if has_selected_recipes:
                raise ValueError("selected_recipes is only allowed when concept_source is 'cookbook'")
            if has_selected_authored_recipe:
                raise ValueError(
                    "selected_authored_recipe is only allowed when concept_source is 'authored'"
                )
            if has_planner_authored_anchor:
                raise ValueError(
                    "planner_authored_recipe_anchor is only allowed when concept_source is 'planner_authored_anchor'"
                )
            if has_planner_cookbook_target:
                raise ValueError(
                    "planner_cookbook_target is only allowed when concept_source is 'planner_cookbook_target'"
                )
        return self


class CreateSessionLegacyRequest(BaseModel):
    model_config = {"extra": "forbid"}

    concept_source: Literal["free_text"] = "free_text"
    free_text: str = Field(max_length=2000)
    guest_count: int = Field(ge=1, le=100)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


class CreateSessionAuthoredSelection(BaseModel):
    model_config = {"extra": "forbid"}

    recipe_id: uuid.UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CreateSessionCookbookSelection(BaseModel):
    chunk_id: uuid.UUID


class CreateSessionPlannerAuthoredAnchor(BaseModel):
    model_config = {"extra": "forbid"}

    recipe_id: uuid.UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class GenerationRetryReason(BaseModel):
    """Normalized retry context stored in checkpoint state.

    Single-oven auto-repair is intentionally narrow: only typed dag_merger
    `RESOURCE_CONFLICT` failures with one-oven `irreconcilable` metadata are
    retryable. Within-tolerance overlap stays compatible, and second-oven
    kitchens remain outside this retry seam.
    """

    node_name: str
    error_type: ErrorType
    summary: OneOvenConflictSummary
    detail: str
    attempt: int = Field(ge=1)


class GenerationRetryEligibility(BaseModel):
    """Typed router-facing view of whether dag_merger can trigger auto-repair.

    This model keeps retry-routing policy in checkpoint-local state instead of
    implicit conditionals. The one-oven contract is stable and explicit:
    when `has_second_oven` is false, overlapping oven work is only considered
    retryable when the scheduler classified it as `irreconcilable`, meaning the
    required temperatures exceed the configured tolerance and cannot be repaired
    by simple resequencing inside a single oven.
    """

    eligible: bool = False
    exhausted: bool = False
    current_attempt: int = Field(ge=1, default=1)
    attempt_limit: int = Field(ge=1, default=1)
    retry_reason: Optional[GenerationRetryReason] = None


class GenerationAttemptRecord(BaseModel):
    """Lightweight checkpoint-visible record of generation attempts."""

    attempt: int = Field(ge=1)
    trigger: Literal["initial", "auto_repair"] = "initial"
    recipe_names: list[str] = []
    retry_reason: Optional[GenerationRetryReason] = None


class CreateSessionPlannerCookbookTarget(BaseModel):
    model_config = {"extra": "forbid"}

    cookbook_id: uuid.UUID
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    mode: PlannerLibraryCookbookPlanningMode


class CreateSessionCookbookRequest(BaseModel):
    model_config = {"extra": "forbid"}

    concept_source: Literal["cookbook"] = "cookbook"
    free_text: str = Field(max_length=2000)
    selected_recipes: list[CreateSessionCookbookSelection] = Field(min_length=1)
    guest_count: int = Field(ge=1, le=100)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


class CreateSessionAuthoredRequest(BaseModel):
    model_config = {"extra": "forbid"}

    concept_source: Literal["authored"] = "authored"
    free_text: str = Field(max_length=2000)
    selected_authored_recipe: CreateSessionAuthoredSelection
    guest_count: int = Field(ge=1, le=100)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


class CreateSessionPlannerAuthoredAnchorRequest(BaseModel):
    model_config = {"extra": "forbid"}

    concept_source: Literal["planner_authored_anchor"] = "planner_authored_anchor"
    free_text: str = Field(max_length=2000)
    planner_authored_recipe_anchor: CreateSessionPlannerAuthoredAnchor
    guest_count: int = Field(ge=1, le=100)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


class CreateSessionPlannerCookbookTargetRequest(BaseModel):
    model_config = {"extra": "forbid"}

    concept_source: Literal["planner_cookbook_target"] = "planner_cookbook_target"
    free_text: str = Field(max_length=2000)
    planner_cookbook_target: CreateSessionPlannerCookbookTarget
    guest_count: int = Field(ge=1, le=100)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


CreateSessionRequest = Annotated[
    CreateSessionLegacyRequest
    | CreateSessionCookbookRequest
    | CreateSessionAuthoredRequest
    | CreateSessionPlannerAuthoredAnchorRequest
    | CreateSessionPlannerCookbookTargetRequest,
    Field(discriminator=None),
]


class InitialPipelineState(TypedDict):
    concept: dict
    kitchen_config: dict
    equipment: list[dict]
    user_id: str
    rag_owner_key: str
    raw_recipes: list[dict]
    enriched_recipes: list[dict]
    validated_recipes: list[dict]
    recipe_dags: list[dict]
    merged_dag: Optional[dict]
    schedule: Optional[dict]
    errors: list[dict]
    generation_attempt: int
    generation_attempt_limit: int
    generation_retry_reason: Optional[dict]
    generation_retry_exhausted: bool
    generation_history: list[dict]


def build_initial_pipeline_state(
    concept: DinnerConcept,
    user_id: str,
    rag_owner_key: str,
    kitchen_config: dict,
    equipment: list[dict],
) -> InitialPipelineState:
    """Build the initial GRASPState payload passed to LangGraph."""
    return {
        "concept": concept.model_dump(mode="json"),
        "kitchen_config": kitchen_config,
        "equipment": equipment,
        "user_id": user_id,
        "rag_owner_key": rag_owner_key,
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
        "errors": [],
        "generation_attempt": 1,
        "generation_attempt_limit": 3,
        "generation_retry_reason": None,
        "generation_retry_exhausted": False,
        "generation_history": [],
    }


def build_session_initial_state(
    concept_payload: dict,
    user_id: str,
    rag_owner_key: str,
    kitchen_config: dict,
    equipment: list[dict],
) -> tuple[DinnerConcept, InitialPipelineState]:
    """
    Validate persisted Session.concept_json and build the initial GRASP state.

    This is the worker entry seam for both legacy free-text and cookbook-selected
    sessions. It intentionally keeps cookbook selections inside the concept and
    leaves raw_recipes empty so the graph still enters through recipe_generator,
    which can deterministically seed cookbook recipes without LLM generation.
    """
    concept = DinnerConcept.model_validate(concept_payload)
    return concept, build_initial_pipeline_state(
        concept=concept,
        user_id=user_id,
        rag_owner_key=rag_owner_key,
        kitchen_config=kitchen_config,
        equipment=equipment,
    )


# ── GRASPState ────────────────────────────────────────────────────────────────
# TypedDict required by LangGraph. All Pydantic models stored as dicts for
# maximum JSON serialisation compatibility with LangGraph's PostgresSaver.
# Nodes must call Model.model_validate(state["field"]) to get typed instances.
#
# This avoids the hidden deserialization trap: LangGraph restores checkpoint
# state as plain Python dicts, NOT Pydantic model instances. If nodes assume
# they receive Pydantic objects, they will crash on resume from checkpoint.
# Storing as dicts and validating at node boundaries is the safe pattern.


class GRASPState(TypedDict, total=False):
    concept: dict  # DinnerConcept.model_dump()
    kitchen_config: dict  # KitchenConfig fields
    equipment: list[dict]  # List[Equipment-like dicts] snapshotted at session start
    user_id: str  # UUID string for relational ownership
    rag_owner_key: str  # Stable Pinecone ownership key portable across DB migrations
    raw_recipes: list[dict]  # List[RawRecipe.model_dump()]
    enriched_recipes: list[dict]  # List[EnrichedRecipe.model_dump()]
    validated_recipes: list[dict]  # List[ValidatedRecipe.model_dump()]
    recipe_dags: list[dict]  # List[RecipeDAG.model_dump()]
    merged_dag: Optional[dict]  # MergedDAG.model_dump() | None
    schedule: Optional[dict]  # NaturalLanguageSchedule.model_dump() | None
    errors: Annotated[list[dict], operator.add]  # ACCUMULATOR — NodeError.model_dump()
    token_usage: Annotated[list[dict], operator.add]  # ACCUMULATOR — per-node LLM token counts
    generation_attempt: int  # current generation attempt number (1-indexed)
    generation_attempt_limit: int  # bounded retry ceiling for corrective regeneration
    generation_retry_reason: Optional[dict]  # GenerationRetryReason.model_dump() | None
    generation_retry_exhausted: bool  # set when an eligible retry path has no attempts left
    generation_history: list[dict]  # List[GenerationAttemptRecord.model_dump()]
