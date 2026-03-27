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
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import MealType, Occasion


class SelectedCookbookRecipe(BaseModel):
    """Authoritative cookbook recipe reference captured at session creation."""

    chunk_id: uuid.UUID
    book_id: uuid.UUID
    book_title: str = Field(max_length=500)
    text: str = Field(min_length=1)
    chapter: str = ""
    page_number: int = Field(ge=0, default=0)


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
    concept_source: Literal["free_text", "cookbook"] = "free_text"
    selected_recipes: list[SelectedCookbookRecipe] = []

    @field_validator("serving_time")
    @classmethod
    def validate_serving_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("serving_time must be in HH:MM 24-hour format (e.g. '19:00')")
        return v

    @model_validator(mode="after")
    def validate_cookbook_source_contract(self) -> "DinnerConcept":
        if self.concept_source == "cookbook" and not self.selected_recipes:
            raise ValueError("selected_recipes is required when concept_source is 'cookbook'")
        if self.concept_source == "free_text" and self.selected_recipes:
            raise ValueError("selected_recipes is only allowed when concept_source is 'cookbook'")
        return self


class CreateSessionLegacyRequest(BaseModel):
    free_text: str = Field(max_length=2000)
    guest_count: int = Field(ge=1, le=100)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


class CreateSessionCookbookSelection(BaseModel):
    chunk_id: uuid.UUID


class CreateSessionCookbookRequest(BaseModel):
    selected_recipes: list[CreateSessionCookbookSelection] = Field(min_length=1)
    guest_count: int = Field(ge=1, le=100)
    meal_type: MealType
    occasion: Occasion
    dietary_restrictions: list[str] = []
    serving_time: Optional[str] = None


CreateSessionRequest = Annotated[
    CreateSessionLegacyRequest | CreateSessionCookbookRequest,
    Field(discriminator=None),
]


def build_initial_pipeline_state(
    concept: DinnerConcept,
    user_id: str,
    rag_owner_key: str,
    kitchen_config: dict,
    equipment: list[dict],
) -> dict:
    """Build the initial GRASPState payload passed to LangGraph."""
    return {
        "concept": concept.model_dump(),
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
        "test_mode": None,
    }


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
# The one exception to dict storage is test_mode — it's a plain str | None.

from typing import TypedDict


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
    test_mode: Optional[str]  # Phase 3 only. None in production.
