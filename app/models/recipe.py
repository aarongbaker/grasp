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
    """Flat ingredient specification from the generator. No units normalization here —
    that happens in the enricher's _parse_and_normalize_ingredients() using Pint."""

    name: str
    quantity: str  # free-form string: "500g", "2 tbsp", "to taste"
    preparation: str = ""  # optional prep note: "finely diced", "at room temperature"


class IngredientUse(BaseModel):
    """
    Structured ingredient metadata extracted from recipe steps by the enricher.
    Maps natural language ingredient references to canonical units for prep merging.

    Why canonical units? The dag_merger can merge identical prep steps across recipes
    (e.g. "dice 1 cup onion for recipe A and 2 cups for recipe B → dice 3 cups total").
    For merging to work, quantities must be in the same units — hence normalization.

    quantity_canonical is None when the unit is unconvertible (e.g. "a pinch", "to taste").
    In that case fallback_reason explains why, and quantity_original is preserved verbatim.
    """

    ingredient_name: str  # normalized name from ingredient-parser-nlp (e.g. "celery")
    prep_method: str      # extracted preparation (e.g. "diced", "chopped fine")
    quantity_canonical: Optional[float] = None  # Pint-normalized amount, None if unconvertible
    unit_canonical: Optional[str] = None  # canonical unit: "cup", "tbsp", "tsp", "gram"
    quantity_original: str  # verbatim original: "2 cups", "50g" — always preserved
    fallback_reason: Optional[str] = None  # why normalization failed (e.g. "unconvertible unit: 'pinch'")


class RecipeStep(BaseModel):
    """
    The scheduling atom. Every scheduling decision flows from these fields.
    Hidden detail: duration_max can equal duration_minutes (deterministic step)
    OR be None (same meaning). Both must be handled identically downstream.

    step_id format: {recipe_slug}_step_{n} — used as edge keys in RecipeDAG.
    The slug prefix prevents ID collisions across recipes in the merged DAG.
    """

    step_id: str  # Format: {recipe_slug}_step_{n} — used as DAG edge keys
    description: str  # refined action text from enricher (includes temperatures, visual cues)

    duration_minutes: int  # optimistic/expected duration — scheduler uses this for timing
    # duration_max: None = deterministic step (same as duration_minutes).
    # Set = variable-duration step (e.g. braising, proofing). Renderer shows
    # "10–14 min" heads_up. Scheduler uses duration_minutes for layout, duration_max
    # for worst-case total calculation. Must be >= duration_minutes if set.
    duration_max: Optional[int] = None

    # List of step_ids that must complete before this step can start.
    # First step has []. Validated by EnrichedRecipe.validate_depends_on_references()
    # and again by dag_builder._build_single_dag() via NetworkX cycle detection.
    depends_on: list[str] = []

    resource: Resource  # determines which capacity pool this step competes for

    # Equipment names required by this step (e.g. "stand_mixer"). Capacity=1 each —
    # steps requiring the same equipment cannot overlap. Validated at dag_merger time.
    required_equipment: list[str] = []

    # Prep-ahead fields — set by enricher when the step genuinely benefits from
    # doing it far in advance (brining, marinating, making stock). Quick-prep tasks
    # like chopping get can_be_done_ahead=False even if technically possible.
    can_be_done_ahead: bool = False
    prep_ahead_window: Optional[str] = None  # e.g. "up to 24 hours", "up to 3 days"
    prep_ahead_notes: Optional[str] = None   # storage/reheating instructions

    # Ingredient metadata linked by the enricher's _link_steps_to_ingredients().
    # Used by the dag_merger's prep-step merging logic. Empty list if not populated.
    ingredient_uses: list[IngredientUse] = []

    # Fahrenheit oven temperature extracted from the step description by the enricher.
    # None for non-oven steps. Used by dag_merger for oven temperature conflict detection.
    oven_temp_f: Optional[int] = None

    @field_validator("duration_minutes")
    @classmethod
    def duration_must_be_positive(cls, v: int) -> int:
        # Scheduling math breaks if duration is 0 (infinite loop risk in greedy scheduler)
        # or negative (impossible). Caught here before reaching the DAG builder.
        if v <= 0:
            raise ValueError(f"duration_minutes must be > 0, got {v}")
        return v

    @field_validator("duration_max")
    @classmethod
    def duration_max_must_exceed_min(cls, v: Optional[int], info) -> Optional[int]:
        # A step cannot have a worst-case shorter than its optimistic estimate.
        # info.data.get() because duration_minutes may not yet be validated if it
        # failed its own validator — check for None before comparing.
        if v is not None:
            duration_minutes = info.data.get("duration_minutes")
            if duration_minutes is not None and v < duration_minutes:
                raise ValueError(f"duration_max ({v}) must be >= duration_minutes ({duration_minutes})")
        return v


class RecipeProvenance(BaseModel):
    """Canonical per-recipe origin carried through runtime, persistence, and result reads.

    kind distinguishes how the recipe entered the pipeline:
      - "generated": Claude LLM produced it from free_text concept
      - "library_authored": user's private authored recipe, compiled deterministically
      - "library_cookbook": cookbook chunk converted to RawRecipe (no LLM generation)
    """

    kind: Literal["generated", "library_authored", "library_cookbook"]
    source_label: Optional[str] = None   # human label (e.g. book title or "My Recipe")
    recipe_id: Optional[str] = None      # UUID string if library_authored
    cookbook_id: Optional[str] = None    # UUID string if library_cookbook


class RawRecipe(BaseModel):
    """Generator output. Steps are flat strings — no timing or resource tags yet.

    This is the unstructured hand-off from recipe_generator_node to enrich_recipe_steps_node.
    Steps are plain text (e.g. "Sear the beef on all sides over high heat.") —
    the enricher converts these into structured RecipeStep objects with timing,
    resource assignments, and dependency edges.
    """

    name: str
    description: str
    servings: int
    cuisine: str
    estimated_total_minutes: int  # rough total — enricher uses this to calibrate step durations
    ingredients: list[Ingredient]
    steps: list[str]  # flat strings — EnrichedRecipe converts to RecipeStep

    # Default provenance = LLM generated. Overridden for cookbook/authored sessions.
    provenance: RecipeProvenance = RecipeProvenance(kind="generated")

    # Course tag added by generator. Must include exactly one "entree" per menu
    # (enforced by generator prompt). Used by dag_merger for oven conflict classification.
    course: Optional[Literal["appetizer", "soup", "salad", "entree", "side", "dessert", "other"]] = None


class EnrichedRecipe(BaseModel):
    """
    Enricher output. Flat strings → structured RecipeStep objects.

    Hidden detail: model_validator runs AFTER all field validators. The
    depends_on consistency check must run after steps is fully populated,
    so it uses mode='after'. This is the validator mock_validator.py runs
    for real — it is NOT a stub.

    Composition: source preserves the full RawRecipe so a future diff view
    can show exactly what enrichment changed (timing, resources, dependencies)
    versus what the generator produced.
    """

    source: RawRecipe  # full raw preserved — composition pattern (not inheritance)
    steps: list[RecipeStep]  # enriched structured steps, one per raw step (+ injected preheat)

    # Compatibility field preserved while cookbook-RAG removal settles.
    # Active enrichment is LLM-only, so this should normally be an empty list.
    rag_sources: list[str] = []

    chef_notes: str = ""          # Claude's practical advice for executing the recipe
    techniques_used: list[str] = []  # e.g. ["braising", "emulsification"]

    @model_validator(mode="after")
    def validate_depends_on_references(self) -> "EnrichedRecipe":
        """
        All step_ids referenced in depends_on must exist in the recipe's
        steps list. Dangling references cause silent KeyErrors in DAG builder.
        This validator catches them at the data layer before they propagate.

        Runs mode='after' because it needs the full steps list to be populated
        first — field validators run per-field, not over the whole model.
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

    Wraps EnrichedRecipe with:
      - validated_at: UTC timestamp for audit/debugging
      - warnings: reserved for future soft checks (e.g. "step seems very long")
      - passed: explicit flag so downstream nodes can filter without re-validating

    The DAG builder calls ValidatedRecipe.model_validate(dict) to get typed access,
    then digs into .source (EnrichedRecipe) → .steps for DAG construction.
    """

    source: EnrichedRecipe  # full enriched recipe preserved — composition chain continues
    validated_at: datetime  # UTC timestamp; set by validator_node
    warnings: list[str] = []  # non-fatal advisory notes (empty in V1)
    passed: bool = True  # False means Pydantic rejected the recipe; excluded from pipeline
