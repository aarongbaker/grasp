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
    """Planner intent for how tightly cookbook scope should constrain generation.

    STRICT: generator must only use dishes explicitly from the named cookbook —
            no free-text LLM creativity. Used when the chef wants an exact
            cookbook execution, not inspiration.
    COOKBOOK_BIASED: generator prefers cookbook recipes but can fill gaps with
                     LLM generation. Used for loose "inspired by" mode.
    """

    STRICT = "strict"
    COOKBOOK_BIASED = "cookbook_biased"


class SelectedCookbookRecipe(BaseModel):
    """Authoritative cookbook recipe reference captured at session creation.

    Snapshot of chunk metadata at the time the session was created.
    This prevents cookbook edits or deletions from silently altering
    session history — the session record is self-contained.
    """

    chunk_id: uuid.UUID
    book_id: uuid.UUID
    book_title: str = Field(max_length=500)
    text: str = Field(min_length=1)
    chapter: str = ""
    page_number: int = Field(ge=0, default=0)


class SelectedAuthoredRecipe(BaseModel):
    """Minimal authored recipe selection persisted into the session concept.

    Only recipe_id and title are stored — the full authored recipe payload
    is resolved at pipeline start from AuthoredRecipeRecord. This keeps the
    concept lightweight and makes it easy to see what was selected without
    deserializing the entire recipe.
    """

    recipe_id: uuid.UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PlannerLibraryAuthoredRecipeAnchor(BaseModel):
    """Planner-lane reference to one owned authored recipe in the private library.

    The planner_authored_anchor concept_source causes the generator to use this
    authored recipe as the "anchor" dish and fill the rest of the menu with LLM
    generation. Different from 'authored' which generates a schedule FROM the
    authored recipe only.
    """

    recipe_id: uuid.UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PlannerLibraryCookbookTarget(BaseModel):
    """Planner-lane reference to one owned authored-recipe cookbook container.

    Points at a user's private recipe cookbook (RecipeCookbookRecord). When
    concept_source='planner_cookbook_target', the generator either picks from
    this cookbook exclusively (STRICT) or uses it as a preferred source (COOKBOOK_BIASED).
    """

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
    """Request body for POST /sessions/planner/resolve.

    The planner resolution endpoint exists so the frontend can resolve a
    natural-language reference (e.g. "my pasta book") to an exact recipe_id or
    cookbook_id before session creation. This keeps concept_source validation
    strict — the session creation endpoint only accepts exact IDs.
    """

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


# Discriminated union — FastAPI/Pydantic selects the correct match type from
# the `kind` field. Discriminators avoid ambiguous deserialization errors when
# both shapes have similar fields.
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

    concept_source determines which code path the generator node uses:
      "free_text"             → LLM generates all recipes from scratch
      "cookbook"              → generator seeds from selected_recipes deterministically
      "authored"              → generator compiles authored_payload into pipeline format
      "planner_authored_anchor" → authored recipe is one dish; LLM fills the rest
      "planner_cookbook_target" → cookbook provides pool; LLM selects/fills gaps

    The validate_source_contract validator enforces that exactly the right
    optional fields are present for each concept_source. This prevents silent
    misconfiguration (e.g. sending selected_recipes with free_text mode)
    that would be silently ignored without the explicit check.
    """

    free_text: str = Field(max_length=2000)
    guest_count: int = Field(ge=1, le=100)
    dish_count: int | None = Field(default=None, ge=1, le=12)
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
        """Enforce that each concept_source has exactly its required optional fields.

        This runs AFTER all field-level validators, so all fields are fully typed.
        The nested if/elif chain checks each source's required and forbidden fields.
        Without this check, the generator node would receive ambiguous state
        (e.g. both selected_recipes and planner_cookbook_target populated) and
        have to guess which one to use — defensive validation is better.
        """
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
            # free_text — no optional reference fields allowed
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


# ── Session Creation Request Models ──────────────────────────────────────────
# Each CreateSession*Request variant corresponds to one concept_source.
# They are separate models (not a single polymorphic model) because each has
# a different set of required fields — a unified model would make too many
# fields optional and rely on runtime validation to catch misconfiguration.
#
# The CreateSessionRequest union is the FastAPI body type. FastAPI tries each
# variant in order and uses the first one that validates. discriminator=None
# means no Literal field is used for routing — FastAPI tries sequentially.
# This is safe because each variant has a different Literal concept_source field.


class CreateSessionLegacyRequest(BaseModel):
    """Legacy free-text session creation. concept_source is always 'free_text'."""

    model_config = {"extra": "forbid"}

    concept_source: Literal["free_text"] = "free_text"
    free_text: str = Field(max_length=2000)
    guest_count: int = Field(ge=1, le=100)
    dish_count: int | None = Field(default=None, ge=1, le=12)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


class CreateSessionAuthoredSelection(BaseModel):
    """Inlined authored recipe reference for the authored session creation endpoint."""

    model_config = {"extra": "forbid"}

    recipe_id: uuid.UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CreateSessionCookbookSelection(BaseModel):
    """Inlined chunk reference for cookbook-mode session creation.

    Only chunk_id is required at request time — full chunk metadata is resolved
    from the database before being embedded into the DinnerConcept snapshot.
    """

    chunk_id: uuid.UUID


class CreateSessionPlannerAuthoredAnchor(BaseModel):
    """Inlined authored recipe anchor for planner_authored_anchor sessions."""

    model_config = {"extra": "forbid"}

    recipe_id: uuid.UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class GenerationRetryReason(BaseModel):
    """Normalized retry context stored in checkpoint state.

    Single-oven auto-repair is intentionally narrow: only typed dag_merger
    `RESOURCE_CONFLICT` failures with one-oven `irreconcilable` metadata are
    retryable. Within-tolerance overlap stays compatible, and second-oven
    kitchens remain outside this retry seam.

    This model is stored in GRASPState.generation_retry_reason and passed to
    the generator's retry prompt via _build_retry_system_prompt(). The fields
    here are the minimum information the generator needs to understand WHY
    its previous attempt failed and HOW to fix it.
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

    `exhausted` is distinct from `eligible=False` — exhausted means we TRIED
    the retry path but ran out of attempts. eligible=False means we never
    entered the retry path (compatible conflict or second oven available).
    """

    eligible: bool = False
    exhausted: bool = False
    current_attempt: int = Field(ge=1, default=1)
    attempt_limit: int = Field(ge=1, default=1)
    retry_reason: Optional[GenerationRetryReason] = None


class GenerationAttemptRecord(BaseModel):
    """Lightweight checkpoint-visible record of generation attempts.

    Appended to GRASPState.generation_history after each generator run.
    Used for observability — operators can inspect the checkpoint to see
    how many attempts were made and what recipes were generated each time.
    Not used by routing logic (that reads generation_attempt directly).
    """

    attempt: int = Field(ge=1)
    trigger: Literal["initial", "auto_repair"] = "initial"
    recipe_names: list[str] = []
    retry_reason: Optional[GenerationRetryReason] = None


class CreateSessionPlannerCookbookTarget(BaseModel):
    """Inlined cookbook target reference for planner_cookbook_target sessions."""

    model_config = {"extra": "forbid"}

    cookbook_id: uuid.UUID
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    mode: PlannerLibraryCookbookPlanningMode


class CreateSessionCookbookRequest(BaseModel):
    """Request for cookbook-mode sessions. Requires at least one selected chunk."""

    model_config = {"extra": "forbid"}

    concept_source: Literal["cookbook"] = "cookbook"
    free_text: str = Field(max_length=2000)
    selected_recipes: list[CreateSessionCookbookSelection] = Field(min_length=1)
    guest_count: int = Field(ge=1, le=100)
    dish_count: int | None = Field(default=None, ge=1, le=12)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


class CreateSessionAuthoredRequest(BaseModel):
    """Request for authored-mode sessions. Requires one authored recipe selection."""

    model_config = {"extra": "forbid"}

    concept_source: Literal["authored"] = "authored"
    free_text: str = Field(max_length=2000)
    selected_authored_recipe: CreateSessionAuthoredSelection
    guest_count: int = Field(ge=1, le=100)
    dish_count: int | None = Field(default=None, ge=1, le=12)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


class CreateSessionPlannerAuthoredAnchorRequest(BaseModel):
    """Request for planner_authored_anchor sessions. Requires an authored recipe anchor."""

    model_config = {"extra": "forbid"}

    concept_source: Literal["planner_authored_anchor"] = "planner_authored_anchor"
    free_text: str = Field(max_length=2000)
    planner_authored_recipe_anchor: CreateSessionPlannerAuthoredAnchor
    guest_count: int = Field(ge=1, le=100)
    dish_count: int | None = Field(default=None, ge=1, le=12)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


class CreateSessionPlannerCookbookTargetRequest(BaseModel):
    """Request for planner_cookbook_target sessions. Requires a cookbook target."""

    model_config = {"extra": "forbid"}

    concept_source: Literal["planner_cookbook_target"] = "planner_cookbook_target"
    free_text: str = Field(max_length=2000)
    planner_cookbook_target: CreateSessionPlannerCookbookTarget
    guest_count: int = Field(ge=1, le=100)
    dish_count: int | None = Field(default=None, ge=1, le=12)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


# Discriminated union for all session creation variants.
# FastAPI validates request bodies against each type in sequence.
# discriminator=None disables Pydantic's discriminated union logic —
# each variant has a Literal concept_source field, so FastAPI's
# sequential validation still produces the correct type.
CreateSessionRequest = Annotated[
    CreateSessionLegacyRequest
    | CreateSessionCookbookRequest
    | CreateSessionAuthoredRequest
    | CreateSessionPlannerAuthoredAnchorRequest
    | CreateSessionPlannerCookbookTargetRequest,
    Field(discriminator=None),
]


class InitialPipelineState(TypedDict):
    """Typed view of the initial state dict passed to graph.ainvoke().

    All fields are plain Python types (str, list[dict], etc.) not Pydantic
    objects, because LangGraph's PostgresSaver serializes state to JSON.
    Pydantic objects survive initial invocation but are deserialized as plain
    dicts on checkpoint resume — storing plain types avoids this asymmetry.
    """

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
    """Build the initial GRASPState payload passed to LangGraph.

    All list fields start empty — nodes replace them (never append).
    generation_attempt_limit=3 allows up to 2 corrective retries for
    one-oven conflicts (initial + 2 auto-repair attempts).
    """
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

    Raises ValidationError if the stored concept_json is invalid (e.g. from a
    schema migration). The Celery task wrapper catches this and writes FAILED.
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
#
# ACCUMULATOR FIELDS (Annotated with operator.add):
#   errors:      each node appends its NodeError dicts; router reads errors[-1]
#   token_usage: each LLM node appends its token count dict for billing
#
# All other fields use REPLACE semantics — the last node to write wins.
# This is why nodes return {"recipe_dags": new_list} not {"recipe_dags": old + new}.


class GRASPState(TypedDict, total=False):
    concept: dict                      # DinnerConcept.model_dump()
    kitchen_config: dict               # KitchenConfig fields — snapshotted at session start
    equipment: list[dict]              # Equipment dicts — snapshotted at session start
    user_id: str                       # UUID string for relational ownership
    rag_owner_key: str                 # Stable Pinecone key, portable across DB migrations
    raw_recipes: list[dict]            # list[RawRecipe.model_dump()]
    enriched_recipes: list[dict]       # list[EnrichedRecipe.model_dump()]
    validated_recipes: list[dict]      # list[ValidatedRecipe.model_dump()]
    recipe_dags: list[dict]            # list[RecipeDAG.model_dump()]
    merged_dag: Optional[dict]         # MergedDAG.model_dump() | None
    schedule: Optional[dict]           # NaturalLanguageSchedule.model_dump() | None

    # ACCUMULATOR: LangGraph's operator.add reducer APPENDS new errors to the
    # existing list instead of replacing it. This is what makes per-recipe
    # error isolation work — each node can push errors independently and
    # the router reads errors[-1] to see the most recent failure.
    errors: Annotated[list[dict], operator.add]

    # ACCUMULATOR: each LLM node appends a dict like:
    # {"node": "recipe_generator", "input_tokens": 1234, "output_tokens": 567}
    # finalise_session() sums these for Session.token_usage.
    token_usage: Annotated[list[dict], operator.add]

    generation_attempt: int            # current attempt (1-indexed, incremented on retry)
    generation_attempt_limit: int      # ceiling — 3 allows initial + 2 corrective retries
    generation_retry_reason: Optional[dict]  # GenerationRetryReason.model_dump() | None
    generation_retry_exhausted: bool   # True when retry path entered but no attempts left
    generation_history: list[dict]     # list[GenerationAttemptRecord.model_dump()] for observability
