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
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.models.enums import ErrorType, MealType, Occasion, SessionStatus
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


class PlannerCatalogCookbookReference(BaseModel):
    """Planner-lane reference to one platform-managed catalog cookbook.

    This is intentionally separate from planner_cookbook_target, which is reserved
    for private chef-owned RecipeCookbookRecord containers. Sessions persist the
    canonical catalog summary plus derived access state from the backend fixture seam.
    """

    catalog_cookbook_id: uuid.UUID
    slug: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    access_state: Literal["included", "preview", "locked"]
    access_state_reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]


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
      "free_text"               → LLM generates all recipes from scratch
      "cookbook"                → generator seeds from selected_recipes deterministically
      "authored"                → generator compiles authored_payload into pipeline format
      "planner_authored_anchor" → authored recipe is one dish; LLM fills the rest
      "planner_cookbook_target" → private cookbook provides pool; LLM selects/fills gaps
      "planner_catalog_cookbook" → platform catalog cookbook selected through backend entitlement seam

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
        "free_text",
        "cookbook",
        "authored",
        "planner_authored_anchor",
        "planner_cookbook_target",
        "planner_catalog_cookbook",
    ] = "free_text"
    selected_recipes: list[SelectedCookbookRecipe] = []
    selected_authored_recipe: Optional[SelectedAuthoredRecipe] = None
    planner_authored_recipe_anchor: Optional[PlannerLibraryAuthoredRecipeAnchor] = None
    planner_cookbook_target: Optional[PlannerLibraryCookbookTarget] = None
    planner_catalog_cookbook: Optional[PlannerCatalogCookbookReference] = None

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
        """Enforce that each concept_source has exactly its required optional fields."""
        has_selected_recipes = bool(self.selected_recipes)
        has_selected_authored_recipe = self.selected_authored_recipe is not None
        has_planner_authored_anchor = self.planner_authored_recipe_anchor is not None
        has_planner_cookbook_target = self.planner_cookbook_target is not None
        has_planner_catalog_cookbook = self.planner_catalog_cookbook is not None

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
            if has_planner_catalog_cookbook:
                raise ValueError(
                    "planner_catalog_cookbook is only allowed when concept_source is 'planner_catalog_cookbook'"
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
            if has_planner_catalog_cookbook:
                raise ValueError(
                    "planner_catalog_cookbook is only allowed when concept_source is 'planner_catalog_cookbook'"
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
            if has_planner_catalog_cookbook:
                raise ValueError(
                    "planner_catalog_cookbook is only allowed when concept_source is 'planner_catalog_cookbook'"
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
            if has_planner_catalog_cookbook:
                raise ValueError(
                    "planner_catalog_cookbook is only allowed when concept_source is 'planner_catalog_cookbook'"
                )
        elif self.concept_source == "planner_catalog_cookbook":
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
            if not has_planner_catalog_cookbook:
                raise ValueError(
                    "planner_catalog_cookbook is required when concept_source is 'planner_catalog_cookbook'"
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
            if has_planner_catalog_cookbook:
                raise ValueError(
                    "planner_catalog_cookbook is only allowed when concept_source is 'planner_catalog_cookbook'"
                )
        return self


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
    node_name: str
    error_type: ErrorType
    summary: OneOvenConflictSummary
    detail: str
    attempt: int = Field(ge=1)


class GenerationRetryEligibility(BaseModel):
    eligible: bool = False
    exhausted: bool = False
    current_attempt: int = Field(ge=1, default=1)
    attempt_limit: int = Field(ge=1, default=1)
    retry_reason: Optional[GenerationRetryReason] = None


class GenerationAttemptRecord(BaseModel):
    attempt: int = Field(ge=1)
    trigger: Literal["initial", "auto_repair"] = "initial"
    recipe_names: list[str] = []
    retry_reason: Optional[GenerationRetryReason] = None


class CreateSessionPlannerCookbookTarget(BaseModel):
    model_config = {"extra": "forbid"}

    cookbook_id: uuid.UUID
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    mode: PlannerLibraryCookbookPlanningMode


class CreateSessionPlannerCatalogCookbook(BaseModel):
    model_config = {"extra": "forbid"}

    catalog_cookbook_id: uuid.UUID
    slug: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    access_state: Literal["included", "preview", "locked"]
    access_state_reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]


class CreateSessionCookbookRequest(BaseModel):
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


class CreateSessionPlannerCatalogCookbookRequest(BaseModel):
    model_config = {"extra": "forbid"}

    concept_source: Literal["planner_catalog_cookbook"] = "planner_catalog_cookbook"
    free_text: str = Field(max_length=2000)
    planner_catalog_cookbook: CreateSessionPlannerCatalogCookbook
    guest_count: int = Field(ge=1, le=100)
    dish_count: int | None = Field(default=None, ge=1, le=12)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


CreateSessionRequest = Annotated[
    CreateSessionLegacyRequest
    | CreateSessionCookbookRequest
    | CreateSessionAuthoredRequest
    | CreateSessionPlannerAuthoredAnchorRequest
    | CreateSessionPlannerCookbookTargetRequest
    | CreateSessionPlannerCatalogCookbookRequest,
    Field(discriminator=None),
]


class GenerationRecoveryAction(BaseModel):
    kind: Literal["update_payment_method", "retry_outstanding_balance"]
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    session_id: uuid.UUID


class SessionOutstandingBalanceSummary(BaseModel):
    has_outstanding_balance: bool = False
    can_retry_charge: bool = False
    billing_state: Literal["ready", "charge_pending", "charged", "charge_failed"] | None = None
    reason_code: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] = None
    reason: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] = None
    retry_attempted_at: datetime | None = None
    recovery_action: GenerationRecoveryAction | None = None


class SessionBillingSummary(BaseModel):
    outstanding_balance: SessionOutstandingBalanceSummary = Field(default_factory=SessionOutstandingBalanceSummary)


class SessionRunAcceptedResponse(BaseModel):
    session_id: uuid.UUID
    status: Literal["generating"] = "generating"
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class SessionRunBlockedResponse(BaseModel):
    session_id: uuid.UUID
    status: Literal["blocked"] = "blocked"
    reason_code: Literal["payment_method_required"]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    requires_payment_method: bool = True
    next_action: GenerationRecoveryAction


class SessionDetailResponse(BaseModel):
    session_id: uuid.UUID
    user_id: uuid.UUID
    status: SessionStatus
    concept_json: dict = Field(default_factory=dict)
    schedule_summary: str | None = None
    total_duration_minutes: int | None = None
    error_summary: str | None = None
    result_recipes: list | None = None
    result_schedule: dict | None = None
    token_usage: dict | None = None
    celery_task_id: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    billing: SessionBillingSummary = Field(default_factory=SessionBillingSummary)


class BillingSetupSessionResponse(BaseModel):
    url: str
    setup_state: Literal["requires_action"] = "requires_action"
    payment_method_status: Literal["missing", "saved"]
    session_id: uuid.UUID | None = None
    customer_state: Literal["existing", "created"]


class BillingRecoverySessionResponse(BaseModel):
    url: str
    recovery_state: Literal["requires_payment_update"] = "requires_payment_update"
    session_id: uuid.UUID
    outstanding_balance: SessionOutstandingBalanceSummary


class BillingSetupStatusResponse(BaseModel):
    has_saved_payment_method: bool
    payment_method_label: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] = None


class BillingRecoveryStatusResponse(BaseModel):
    session_id: uuid.UUID
    outstanding_balance: SessionOutstandingBalanceSummary


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
    concept = DinnerConcept.model_validate(concept_payload)
    return concept, build_initial_pipeline_state(
        concept=concept,
        user_id=user_id,
        rag_owner_key=rag_owner_key,
        kitchen_config=kitchen_config,
        equipment=equipment,
    )


class GRASPState(TypedDict, total=False):
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
    errors: Annotated[list[dict], operator.add]
    token_usage: Annotated[list[dict], operator.add]
    generation_attempt: int
    generation_attempt_limit: int
    generation_retry_reason: Optional[dict]
    generation_retry_exhausted: bool
    generation_history: list[dict]
