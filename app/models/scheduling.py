"""
models/scheduling.py
Scheduling domain: RecipeDAG, MergedDAG, NaturalLanguageSchedule.
Pure Pydantic — live in GRASPState, serialised by LangGraph checkpointer.

Key concept: PASSIVE steps are non-exclusive (resting, braising, chilling).
Other steps can run concurrently during PASSIVE windows. This is the primary
source of time savings in multi-course schedules.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.enums import Resource
from app.models.recipe import RecipeStep
from app.models.user import BurnerDescriptor


class OneOvenConflictRemediation(BaseModel):
    """Machine-readable remediation hints for one-oven menus.

    Produced by the dag_merger when it detects overlapping oven usage.
    Consumed by the generator's retry prompt (_build_retry_system_prompt)
    to explain the conflict and suggest specific remediation actions.

    All fields default to conservative/empty values so legacy persisted schedules
    without remediation metadata still validate cleanly.
    """

    requires_resequencing: bool = False  # True = reordering within one oven is feasible
    suggested_actions: list[str] = []   # human-readable corrective suggestions for the retry prompt
    delaying_recipe_names: list[str] = []  # recipes whose oven work was delayed due to conflict
    blocking_recipe_names: list[str] = []  # recipes causing the conflict (typically the entree)
    notes: Optional[str] = None  # freeform scheduler notes for Claude's retry context


class OneOvenConflictSummary(BaseModel):
    """Typed schedule-level summary of one-oven temperature feasibility.

    Classification contract for dag_merger:
    - compatible: single-oven usage is already compatible — all overlapping oven work
      shares temperature within the 15°F tolerance, or extra oven capacity exists.
    - resequence_required: a single-oven plan remains feasible by staging incompatible
      oven work into separate windows (one recipe finishes before the other starts).
    - irreconcilable: the menu should fail with RESOURCE_CONFLICT because one oven
      cannot satisfy the required temperature windows. This triggers dag_merger_router
      to route to retry_generation for corrective LLM regeneration.

    Defaults preserve backward compatibility for persisted schedules generated
    before this metadata existed.
    """

    # The scheduler's verdict on whether one oven can execute this menu.
    # "irreconcilable" is the only classification that triggers auto-repair retry.
    classification: Literal["compatible", "resequence_required", "irreconcilable"] = "compatible"

    # Maximum allowed temperature difference for oven steps to be considered
    # compatible. 15°F matches typical residential oven thermostat accuracy.
    tolerance_f: int = 15

    # Whether this kitchen has a second oven (from KitchenConfig.has_second_oven).
    # If True, oven conflicts are informational — two ovens can run simultaneously.
    has_second_oven: bool = False

    # The actual temperature gap between the conflicting steps (if known).
    # Populated for "irreconcilable" classifications for retry prompt context.
    temperature_gap_f: Optional[int] = None

    # Names of recipes whose oven steps directly cause the conflict.
    # Passed to the retry prompt so Claude knows which dishes to change.
    blocking_recipe_names: list[str] = []

    # step_ids of the conflicting steps (for fine-grained retry context).
    affected_step_ids: list[str] = []

    # Structured hints for the corrective retry prompt.
    remediation: OneOvenConflictRemediation = Field(default_factory=OneOvenConflictRemediation)


class ScheduledStep(BaseModel):
    """A RecipeStep with absolute timing resolved by the DAG Merger.

    The merger produces ScheduledStep objects from _StepInfo dataclasses.
    All time offsets are absolute from T+0 (pipeline start).
    """

    step_id: str
    recipe_name: str
    description: str
    resource: Resource
    duration_minutes: int
    duration_max: Optional[int] = None

    # Absolute time offsets from T+0. end_at_minute = start_at_minute + duration_minutes.
    # The merger's greedy scheduler sets these based on dependency resolution and
    # resource availability.
    start_at_minute: int
    end_at_minute: int
    end_at_minute_max: Optional[int] = None  # start + duration_max — worst-case end time

    # How much this step can overrun before delaying any successor.
    # slack_minutes = min(successor.start_at_minute) - self.end_at_minute
    # Steps on the critical path have slack_minutes=0.
    slack_minutes: int = 0

    required_equipment: list[str] = []  # equipment names used by this step

    can_be_done_ahead: bool = False
    prep_ahead_window: Optional[str] = None
    prep_ahead_notes: Optional[str] = None
    depends_on: list[str] = []

    # Populated when the merger consolidates identical prep steps across recipes.
    # merged_from lists the original step_ids that were merged into this one.
    # allocation maps recipe_name → quantity breakdown (e.g. {"Pasta": "2 cups", "Risotto": "1 cup"}).
    merged_from: list[str] = []
    allocation: dict[str, str] = {}

    # Fahrenheit oven temperature, preserved from RecipeStep.oven_temp_f.
    # Used by the renderer to display temperature on oven timeline entries.
    oven_temp_f: Optional[int] = None

    # Stovetop burner assignment (from BurnerDescriptor pool in kitchen_config).
    # Populated by dag_merger when named burners are configured.
    # None when kitchen uses the fungible fallback pool (max_burners only).
    burner_id: Optional[str] = None          # e.g. "front_left"
    burner_position: Optional[str] = None    # e.g. "front-left"
    burner_size: Optional[str] = None        # e.g. "large"
    burner_label: Optional[str] = None       # display label for the chef
    burner: Optional[BurnerDescriptor] = None  # full snapshot of the assigned descriptor


class RecipeDAG(BaseModel):
    """Per-recipe dependency graph. Built by DAG Builder, consumed by DAG Merger.

    Stores edge topology only — steps live in ValidatedRecipe.source.steps.
    This avoids duplicating step data and keeps the DAG model focused on structure.
    """

    recipe_name: str
    recipe_slug: str  # URL-safe slug derived from recipe_name (used for step_id prefix matching)
    steps: list[RecipeStep]  # intentionally empty in V1 — steps are in ValidatedRecipe
    # Adjacency list: (from_step_id, to_step_id) means from must complete before to.
    # NetworkX DiGraph is constructed at runtime from this structure.
    # JSON round-trip caveat: tuples become lists. Pydantic v2 coerces back to
    # tuple on model_validate(), but raw dict access from GRASPState gives
    # list[list[str]]. Always model_validate() before using typed edges.
    edges: list[tuple[str, str]]  # (from_step_id, to_step_id)


class MergedDAG(BaseModel):
    """
    Cross-recipe resource-aware execution plan. DAG Merger output.
    All step timings are absolute (start_at_minute from T+0).
    total_duration_minutes = max(end_at_minute) across all steps.
    """

    scheduled_steps: list[ScheduledStep]  # sorted by (start_at_minute, recipe_name, step_id)
    total_duration_minutes: int            # makespan: latest step end time
    total_duration_minutes_max: Optional[int] = None  # worst-case makespan using duration_max

    # Sum of non-PASSIVE step durations — the cook's active hands-on time.
    # Displayed separately from total_duration so the chef can plan their energy.
    active_time_minutes: Optional[int] = None

    # Resource utilization windows for conflict checking. Maps resource name →
    # list of (start, end) minute pairs. Same JSON round-trip caveat as RecipeDAG.edges.
    resource_utilisation: dict[str, list[tuple[int, int]]] = {}
    equipment_utilisation: dict[str, list[tuple[int, int]]] = {}

    # Warnings produced when finish-together scheduling intent was frustrated by
    # resource constraints (e.g. "Recipe B will finish 25 min after Recipe A due to oven capacity").
    resource_warnings: list[str] = []

    # One-oven feasibility summary. Populated by dag_merger for every schedule,
    # even compatible ones. Consumed by the renderer for resource_warnings section
    # and by dag_merger_router for retry routing decisions.
    one_oven_conflict: OneOvenConflictSummary = Field(default_factory=OneOvenConflictSummary)


class TimelineEntry(BaseModel):
    """Single entry in the chef-facing schedule. T+0, T+15, T+30... or clock times.

    Produced by the renderer from ScheduledStep objects. Pure presentation layer —
    no scheduling logic here. The renderer decides label, clock_time, and heads_up text.
    """

    time_offset_minutes: int = Field(ge=0)  # absolute offset from T+0
    label: str  # "T+30" in ASAP mode, "6:30 PM" when serving_time is set
    clock_time: Optional[str] = None  # e.g. "6:30 PM" — None in ASAP mode

    step_id: str
    recipe_name: str
    action: str  # refined description; includes allocation breakdown for merged steps

    resource: Resource
    duration_minutes: int
    duration_max: Optional[int] = None
    buffer_minutes: Optional[int] = None  # duration_max - duration_minutes when applicable
    heads_up: Optional[str] = None  # e.g. "Bake 10–14 min depending on oven temperature"

    # Prep-ahead flag and metadata. is_prep_ahead=True only for steps with hour/day windows
    # (not quick-prep steps). Renderer filters these for the prep_ahead_entries list.
    is_prep_ahead: bool = False
    prep_ahead_window: Optional[str] = None
    prep_ahead_notes: Optional[str] = None

    # Burner-aware contract: renderer passes through scheduler-owned burner metadata
    # verbatim for stovetop entries; consumers must not infer burner identity from
    # action text or kitchen config. Always trust this field over any inferred value.
    burner_id: Optional[str] = None
    burner_position: Optional[str] = None
    burner_size: Optional[str] = None
    burner_label: Optional[str] = None
    burner: Optional[BurnerDescriptor] = None

    # Merged prep and oven features (M018):
    # merged_from: original step_ids consolidated into this merged step
    # allocation: recipe_name → quantity string displayed in the action text
    merged_from: list[str] = []
    allocation: dict[str, str] = {}

    # Oven temperature for oven steps. None for stovetop/passive/hands steps.
    oven_temp_f: Optional[int] = None

    # True for injected preheat steps (step_id contains "_preheat_").
    # Lets the UI render preheat entries with a distinct visual treatment.
    is_preheat: bool = False


class NaturalLanguageSchedule(BaseModel):
    """
    The final output the chef sees. Schedule Renderer produces this.
    Prep-ahead steps are surfaced first (before T+0) in a distinct section.
    summary is stored in Session.schedule_summary for the list view.
    """

    # Chronological unified list of all timeline entries (day-of and prep-ahead combined).
    # The frontend can split on is_prep_ahead if it wants a separate "prep-ahead" section.
    timeline: list[TimelineEntry]

    # Filtered subset of timeline where is_prep_ahead=True. Redundant with timeline
    # but pre-computed to avoid client-side filtering on every render.
    prep_ahead_entries: list[TimelineEntry] = []

    total_duration_minutes: int
    total_duration_minutes_max: Optional[int] = None  # worst-case total

    # Active (non-PASSIVE) step duration sum. Displayed separately as "hands-on time".
    active_time_minutes: Optional[int] = None

    # Claude-generated one-paragraph overview. Denormalized to Session.schedule_summary
    # for fast list-view reads without deserializing the full schedule JSON.
    summary: str

    # Populated on PARTIAL outcome (some recipes dropped). Single sentence explaining
    # which recipes were dropped and why. Denormalized to Session.error_summary.
    error_summary: Optional[str] = None

    # One-oven feasibility metadata. Passed through from MergedDAG for frontend
    # display of oven conflict warnings in the schedule detail view.
    one_oven_conflict: OneOvenConflictSummary = Field(default_factory=OneOvenConflictSummary)
