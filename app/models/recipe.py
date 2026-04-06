"""
models/recipe.py
Recipe domain models — pure Pydantic. These live in GRASPState and are
managed by LangGraph's checkpointer. They never touch Postgres directly.

Composition over inheritance (V1.6 decision §2.2):
  RawRecipe → EnrichedRecipe(source: RawRecipe) → ValidatedRecipe(source: EnrichedRecipe)

This preserves the full audit trail at every stage and enables a future
diff view showing exactly what RAG changed vs raw generation.
LangGraph serialises state as JSON between nodes — polymorphic inheritance
creates type ambiguity at deserialisation time; composition avoids it.

RecipeStep is the scheduling atom (§2.3). duration_max enables
uncertainty-aware scheduling — optimistic path uses duration_minutes,
heads_up cues use duration_max as the buffer ceiling.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator

from app.models.enums import Resource


class Ingredient(BaseModel):
    name: str
    quantity: str  # e.g. "500g", "2 tbsp", "to taste"
    preparation: str = ""  # e.g. "finely diced", "at room temperature"


class IngredientUse(BaseModel):
    """
    Structured ingredient metadata extracted from recipe steps.
    Maps natural language ingredient references to canonical units for prep merging.
    """

    ingredient_name: str  # normalized ingredient name (e.g., "celery")
    prep_method: str  # extracted preparation method (e.g., "diced", "chopped")
    quantity_canonical: Optional[float] = None  # normalized quantity (None if unconvertible)
    unit_canonical: Optional[str] = None  # canonical unit (e.g., "cup", "tbsp", "g")
    quantity_original: str  # original quantity string from step (e.g., "2 cups", "50g")
    fallback_reason: Optional[str] = None  # why normalization failed (e.g., "unconvertible unit: 'pinch'")


class RecipeStep(BaseModel):
    """
    The scheduling atom. Every scheduling decision flows from these fields.
    Hidden detail: duration_max can equal duration_minutes (deterministic step)
    OR be None (same meaning). Both must be handled identically downstream.
    """

    step_id: str  # Format: {recipe_slug}_step_{n} — used as DAG edge keys
    description: str
    duration_minutes: int
    duration_max: Optional[int] = None  # None = deterministic. Max buffer for heads_up.
    depends_on: list[str] = []  # step_ids that must complete before this
    resource: Resource
    required_equipment: list[str] = []  # equipment names needed (capacity=1 each)
    can_be_done_ahead: bool = False
    prep_ahead_window: Optional[str] = None  # e.g. "up to 1 week" — used verbatim
    prep_ahead_notes: Optional[str] = None
    ingredient_uses: list[IngredientUse] = []  # extracted ingredient metadata for prep merging
    oven_temp_f: Optional[int] = None  # Fahrenheit integer temp extracted from step description

    @field_validator("duration_minutes")
    @classmethod
    def duration_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"duration_minutes must be > 0, got {v}")
        return v

    @field_validator("duration_max")
    @classmethod
    def duration_max_must_exceed_min(cls, v: Optional[int], info) -> Optional[int]:
        if v is not None:
            duration_minutes = info.data.get("duration_minutes")
            if duration_minutes is not None and v < duration_minutes:
                raise ValueError(f"duration_max ({v}) must be >= duration_minutes ({duration_minutes})")
        return v


class RecipeProvenance(BaseModel):
    """Canonical per-recipe origin carried through runtime, persistence, and result reads."""

    kind: Literal["generated", "library_authored", "library_cookbook"]
    source_label: Optional[str] = None
    recipe_id: Optional[str] = None
    cookbook_id: Optional[str] = None


class RawRecipe(BaseModel):
    """Generator output. Steps are flat strings — no timing or resource tags yet."""

    name: str
    description: str
    servings: int
    cuisine: str
    estimated_total_minutes: int
    ingredients: list[Ingredient]
    steps: list[str]  # flat strings — EnrichedRecipe converts to RecipeStep
    provenance: RecipeProvenance = RecipeProvenance(kind="generated")


class EnrichedRecipe(BaseModel):
    """
    RAG Enricher output. Flat strings → structured RecipeStep objects.

    Hidden detail: model_validator runs AFTER all field validators. The
    depends_on consistency check must run after steps is fully populated,
    so it uses mode='after'. This is the validator mock_validator.py runs
    for real — it is NOT a stub.
    """

    source: RawRecipe  # full raw preserved — composition pattern
    steps: list[RecipeStep]
    rag_sources: list[str] = []  # Pinecone chunk IDs
    chef_notes: str = ""
    techniques_used: list[str] = []

    @model_validator(mode="after")
    def validate_depends_on_references(self) -> "EnrichedRecipe":
        """
        All step_ids referenced in depends_on must exist in the recipe's
        steps list. Dangling references cause silent KeyErrors in DAG builder.
        This validator catches them at the data layer before they propagate.
        """
        step_ids = {step.step_id for step in self.steps}
        for step in self.steps:
            for dep_id in step.depends_on:
                if dep_id not in step_ids:
                    raise ValueError(
                        f"Step '{step.step_id}' depends_on '{dep_id}' "
                        f"which does not exist in this recipe's steps. "
                        f"Available: {sorted(step_ids)}"
                    )
        return self


class ValidatedRecipe(BaseModel):
    """
    Validator output. No LLM call — pure Pydantic validation pass.
    passed=False means validators rejected the recipe (recoverable per-recipe).
    warnings are non-fatal observations surfaced to the chef.
    """

    source: EnrichedRecipe
    validated_at: datetime
    warnings: list[str] = []
    passed: bool = True
