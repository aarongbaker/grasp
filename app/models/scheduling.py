"""
models/scheduling.py
Scheduling domain: RecipeDAG, MergedDAG, NaturalLanguageSchedule.
Pure Pydantic — live in GRASPState, serialised by LangGraph checkpointer.

Key concept: PASSIVE steps are non-exclusive (resting, braising, chilling).
Other steps can run concurrently during PASSIVE windows. This is the primary
source of time savings in multi-course schedules.
"""

from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import Resource
from app.models.recipe import RecipeStep


class ScheduledStep(BaseModel):
    """A RecipeStep with absolute timing resolved by the DAG Merger."""

    step_id: str
    recipe_name: str
    description: str
    resource: Resource
    duration_minutes: int
    duration_max: Optional[int] = None
    start_at_minute: int  # absolute offset from T+0
    end_at_minute: int  # start_at_minute + duration_minutes
    end_at_minute_max: Optional[int] = None  # start + duration_max (worst case)
    slack_minutes: int = 0  # how much this step can overrun before delaying successors
    required_equipment: list[str] = []  # equipment names used by this step
    can_be_done_ahead: bool = False
    prep_ahead_window: Optional[str] = None
    prep_ahead_notes: Optional[str] = None
    depends_on: list[str] = []


class RecipeDAG(BaseModel):
    """Per-recipe dependency graph. Built by DAG Builder, consumed by DAG Merger."""

    recipe_name: str
    recipe_slug: str
    steps: list[RecipeStep]
    # Adjacency list representation for serialisation safety.
    # NetworkX DiGraph is constructed at runtime from this structure.
    # JSON round-trip: tuples become lists. Pydantic v2 coerces back to
    # tuple on model_validate(), but raw dict access from GRASPState gives
    # list[list[str]]. Always model_validate() before using typed edges.
    edges: list[tuple[str, str]]  # (from_step_id, to_step_id)


class MergedDAG(BaseModel):
    """
    Cross-recipe resource-aware execution plan. DAG Merger output.
    All step timings are absolute (start_at_minute from T+0).
    total_duration_minutes = max(end_at_minute) across all steps.
    """

    scheduled_steps: list[ScheduledStep]
    total_duration_minutes: int
    total_duration_minutes_max: Optional[int] = None  # worst-case total
    active_time_minutes: Optional[int] = None  # sum of non-PASSIVE step durations
    resource_utilisation: dict[str, list[tuple[int, int]]] = {}
    # resource → list of (start, end) windows. Used for conflict validation.
    # Same JSON round-trip caveat as RecipeDAG.edges — model_validate() to get tuples.
    equipment_utilisation: dict[str, list[tuple[int, int]]] = {}
    # equipment_name → list of (start, end) windows.


class TimelineEntry(BaseModel):
    """Single entry in the chef-facing schedule. T+0, T+15, T+30... or clock times."""

    time_offset_minutes: int = Field(ge=0)
    label: str  # e.g. "T+30" or "6:30 PM"
    clock_time: Optional[str] = None  # e.g. "6:30 PM" — populated when serving_time is set
    step_id: str
    recipe_name: str
    action: str  # natural language description
    resource: Resource
    duration_minutes: int
    duration_max: Optional[int] = None
    buffer_minutes: Optional[int] = None  # duration_max - duration_minutes when applicable
    heads_up: Optional[str] = None  # e.g. "Bake 10–14 min depending on oven"
    is_prep_ahead: bool = False
    prep_ahead_window: Optional[str] = None


class NaturalLanguageSchedule(BaseModel):
    """
    The final output the chef sees. Schedule Renderer produces this.
    Prep-ahead steps are surfaced first (before T+0) in a distinct section.
    summary is stored in Session.schedule_summary for the list view.
    """

    timeline: list[TimelineEntry]
    prep_ahead_entries: list[TimelineEntry] = []  # prep-ahead steps, separate from day-of
    total_duration_minutes: int
    total_duration_minutes_max: Optional[int] = None  # worst-case total
    active_time_minutes: Optional[int] = None  # non-PASSIVE step durations
    summary: str  # one-paragraph overview for session list view
    error_summary: Optional[str] = None  # populated on PARTIAL outcome
