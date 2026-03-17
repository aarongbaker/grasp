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
from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field

from models.enums import MealType, Occasion


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
    user_id: str  # UUID string for Pinecone RAG filtering
    raw_recipes: list[dict]  # List[RawRecipe.model_dump()]
    enriched_recipes: list[dict]  # List[EnrichedRecipe.model_dump()]
    validated_recipes: list[dict]  # List[ValidatedRecipe.model_dump()]
    recipe_dags: list[dict]  # List[RecipeDAG.model_dump()]
    merged_dag: Optional[dict]  # MergedDAG.model_dump() | None
    schedule: Optional[dict]  # NaturalLanguageSchedule.model_dump() | None
    errors: Annotated[list[dict], operator.add]  # ACCUMULATOR — NodeError.model_dump()
    token_usage: Annotated[list[dict], operator.add]  # ACCUMULATOR — per-node LLM token counts
    test_mode: Optional[str]  # Phase 3 only. None in production.
