"""
graph/nodes/renderer.py
Real schedule renderer — Phase 7. Final node in the pipeline.

Reads merged_dag + errors + concept from GRASPState, converts ScheduledStep
objects into TimelineEntry objects (deterministic), then calls Claude to
generate a natural-language summary and optional error_summary.

Why split deterministic + LLM?
  Timeline construction is algorithmic — each ScheduledStep maps 1:1 to a
  TimelineEntry with clock times, allocation text, and heads_up strings.
  No creativity needed, so no LLM. The summary paragraph IS creative — Claude
  synthesises the whole schedule into a chef-facing overview. Keeping these
  separate means:
    1. Timeline is always produced (even if LLM fails)
    2. Summary failure is recoverable — fallback summary ensures a schedule is returned
    3. Tests can verify timeline independently of LLM mock behavior

Error handling: renderer failure is RECOVERABLE (recoverable=True). A partial
schedule with a fallback summary is always better than no schedule. The
pipeline continues to final_router which routes to mark_partial.

IDEMPOTENCY: Returns schedule as a single dict (replace semantics).

Mockable seam:
  _create_llm()  — returns ChatAnthropic instance.
  Tests patch graph.nodes.renderer._create_llm to bypass the real API.
"""

import logging
from typing import Optional, cast

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.core.llm import extract_token_usage, llm_retry
from app.core.settings import get_settings
from app.models.enums import ErrorType, Resource
from app.models.errors import NodeError
from app.models.pipeline import DinnerConcept, GRASPState
from app.models.scheduling import (
    MergedDAG,
    NaturalLanguageSchedule,
    ScheduledStep,
    TimelineEntry,
)

logger = logging.getLogger(__name__)

# Human-readable descriptions of what makes each resource type variable.
# Used in the heads_up field when duration_max differs from duration_minutes.
# e.g. "45–60 min depending on oven temperature and size"
_RESOURCE_HEADS_UP: dict[Resource, str] = {
    Resource.OVEN: "oven temperature and size",
    Resource.STOVETOP: "stovetop heat",
    Resource.HANDS: "timing",
    Resource.PASSIVE: "conditions",
}


# ── Structured output wrapper ────────────────────────────────────────────────


class ScheduleSummaryOutput(BaseModel):
    """Wrapper for LangChain structured output. Claude returns this shape.

    error_summary is optional because it's only needed when recipes were dropped.
    The prompt instructs Claude to set it to null if no errors occurred.
    """

    summary: str
    error_summary: Optional[str] = None


# ── Clock-time helpers ───────────────────────────────────────────────────────


def _offset_to_clock(offset_minutes: int, start_hour: int, start_minute: int) -> str:
    """Convert a T+offset to a clock time string like '6:30 PM'.

    start_hour/start_minute define T+0 as a wall clock time.
    The 24-hour wrap handles schedules that start before midnight and run past.
    """
    total_minutes = start_hour * 60 + start_minute + offset_minutes
    h = (total_minutes // 60) % 24
    m = total_minutes % 60
    period = "AM" if h < 12 else "PM"
    display_h = h % 12 or 12  # 0 → 12 for midnight/noon display
    return f"{display_h}:{m:02d} {period}"


def _parse_start_time(
    serving_time: str, total_duration_minutes: int
) -> tuple[int, int]:
    """Compute the start time (hour, minute) by subtracting total duration from serving time.

    Example: serving at 19:00 (7 PM) with 90 min total → start at 17:30 (5:30 PM).
    Midnight wrap: a schedule that would start at -30 minutes (before midnight)
    wraps around to 23:30 the previous day. The modulo handles this correctly.
    """
    serving_h, serving_m = map(int, serving_time.split(":"))
    start_total = serving_h * 60 + serving_m - total_duration_minutes
    # Wrap around midnight if needed (e.g. prep starts previous day)
    start_total = start_total % (24 * 60)
    return start_total // 60, start_total % 60


# ── Deterministic timeline construction ──────────────────────────────────────


def _is_meaningful_prep_ahead(step: ScheduledStep) -> bool:
    """Only classify as prep-ahead if the window is hours or days, not minutes.

    The enricher sometimes sets can_be_done_ahead=True for quick prep tasks that
    have a window like "30 minutes ahead". These are NOT meaningful prep-ahead
    steps — they should appear in the day-of timeline, not the prep-ahead section.
    Only steps with hour/day windows (brining, marinating, stock-making) are
    surfaced as prep-ahead entries in the NaturalLanguageSchedule.
    """
    if not step.can_be_done_ahead:
        return False
    if not step.prep_ahead_window:
        return False
    window = step.prep_ahead_window.lower()
    return "hour" in window or "day" in window or "week" in window


def _build_timeline_entry(
    step: ScheduledStep,
    start_time: tuple[int, int] | None = None,
) -> TimelineEntry:
    """Convert a ScheduledStep to a TimelineEntry. Pure, deterministic.

    Clock times are only computed when start_time is provided (serving_time set).
    In ASAP mode (no serving_time), labels are "T+30", "T+45" etc.

    Allocation breakdown for merged steps:
      When the dag_merger consolidates duplicate prep steps across recipes
      (e.g. "chop shallots" appears in both sauce and risotto), it sets
      merged_from and allocation. The action text shows how each recipe's
      quantity contributes: "Chop 2 cups shallots (1 cup for Sauce, 1 cup for Risotto)".

    Preheat detection:
      step_ids containing "_preheat_" are synthetic steps injected by the enricher.
      is_preheat=True lets the frontend render them with a distinct visual treatment
      (oven icon, different background) without parsing step_id strings client-side.
    """
    heads_up = None
    if step.duration_max and step.duration_max != step.duration_minutes:
        heads_up = f"{step.duration_minutes}–{step.duration_max} min depending on {_RESOURCE_HEADS_UP[step.resource]}"

    buffer = None
    if step.duration_max and step.duration_max != step.duration_minutes:
        buffer = step.duration_max - step.duration_minutes

    # Clock-time labels when serving_time is set in the DinnerConcept.
    clock_time = None
    label = f"T+{step.start_at_minute}"
    if start_time is not None:
        clock_time = _offset_to_clock(step.start_at_minute, start_time[0], start_time[1])
        label = clock_time

    # Format allocation breakdown for merged prep steps.
    # Sort by recipe name for deterministic output (avoids flaky tests).
    action = step.description
    if step.merged_from and step.allocation:
        allocation_parts = [f"{qty} for {recipe}" for recipe, qty in sorted(step.allocation.items())]
        allocation_text = ", ".join(allocation_parts)
        action = f"{step.description} ({allocation_text})"

    # Synthetic preheat steps are identified by step_id convention.
    # The enricher injects them with step_id="{slug}_preheat_1".
    is_preheat = "_preheat_" in step.step_id

    return TimelineEntry(
        time_offset_minutes=step.start_at_minute,
        label=label,
        clock_time=clock_time,
        step_id=step.step_id,
        recipe_name=step.recipe_name,
        action=action,
        resource=step.resource,
        duration_minutes=step.duration_minutes,
        duration_max=step.duration_max,
        buffer_minutes=buffer,
        heads_up=heads_up,
        is_prep_ahead=_is_meaningful_prep_ahead(step),
        prep_ahead_window=step.prep_ahead_window,
        prep_ahead_notes=step.prep_ahead_notes,
        # Burner metadata passed through verbatim from the scheduler.
        # Never infer burner identity from action text — trust this field.
        burner_id=step.burner_id,
        burner_position=step.burner_position,
        burner_size=step.burner_size,
        burner_label=step.burner_label,
        burner=step.burner,
        merged_from=step.merged_from,
        allocation=step.allocation,
        oven_temp_f=step.oven_temp_f,
        is_preheat=is_preheat,
    )


def _build_timeline(
    merged_dag: MergedDAG,
    serving_time: str | None = None,
) -> list[TimelineEntry]:
    """Build a unified timeline from a MergedDAG. Deterministic ordering.

    Returns a single sorted list of all TimelineEntry objects.
    The MergedDAG's scheduled_steps are already sorted by
    (start_at_minute, recipe_name, step_id) by the dag_merger.
    We preserve that sort order — no re-sorting needed here.

    Steps with is_prep_ahead=True retain the flag so consumers
    can split the timeline into day-of and prep-ahead sections if desired.
    NaturalLanguageSchedule.prep_ahead_entries is a pre-computed filtered copy.
    """
    start_time = None
    if serving_time:
        start_time = _parse_start_time(serving_time, merged_dag.total_duration_minutes)

    entries: list[TimelineEntry] = []
    for step in merged_dag.scheduled_steps:
        entry = _build_timeline_entry(step, start_time)
        entries.append(entry)
    return entries


# ── Prompt builders ──────────────────────────────────────────────────────────


def _format_schedule_for_prompt(merged_dag: MergedDAG) -> str:
    """Format the scheduled steps as a readable text block for the LLM.

    Claude doesn't need full step detail — resource, duration, and description
    are sufficient for a meaningful summary paragraph.
    Prep-ahead flags are included so Claude can mention them in the summary.
    """
    lines = []
    for step in merged_dag.scheduled_steps:
        resource_label = step.resource.value.upper()
        line = (
            f"  T+{step.start_at_minute}: {step.recipe_name} — "
            f"{step.description} ({resource_label}, {step.duration_minutes} min)"
        )
        if step.can_be_done_ahead and step.prep_ahead_window:
            line += f" [can do ahead: {step.prep_ahead_window}]"
        lines.append(line)
    return "\n".join(lines)


def _format_errors_for_prompt(errors: list[dict]) -> str:
    """Format pipeline errors for the LLM to incorporate into error_summary.

    Claude is given the raw error messages so it can explain dropped recipes
    to the chef in plain English. Without this, Claude would make up reasons.
    """
    if not errors:
        return ""
    lines = []
    for err in errors:
        node = err.get("node_name", "unknown")
        msg = err.get("message", "")
        lines.append(f"  - [{node}] {msg}")
    return "\n".join(lines)


def _build_summary_prompt(
    concept: DinnerConcept,
    merged_dag: MergedDAG,
    errors: list[dict],
) -> str:
    """Build the system prompt for summary generation.

    Structured output (ScheduleSummaryOutput) keeps Claude's response parseable.
    The prompt is prescriptive about what to include so the summary is useful
    to experienced home cooks rather than generic ("this is a schedule for dinner").
    """
    schedule_text = _format_schedule_for_prompt(merged_dag)
    recipe_names = sorted(set(s.recipe_name for s in merged_dag.scheduled_steps))
    has_errors = len(errors) > 0

    error_section = ""
    if has_errors:
        error_text = _format_errors_for_prompt(errors)
        error_section = f"""
## PIPELINE ERRORS (recoverable — some recipes were dropped)
{error_text}

You MUST also produce an `error_summary` field: a single sentence explaining
which recipe(s) were dropped and why, suitable for display to the user."""

    # Resource warnings from the dag_merger — e.g. finish-together intent frustrated.
    resource_warning_section = ""
    if merged_dag.resource_warnings:
        warning_lines = "\n".join(f"  - {w}" for w in merged_dag.resource_warnings)
        resource_warning_section = f"""

## RESOURCE WARNINGS
The following scheduling constraints were detected:
{warning_lines}
"""

    return f"""You are GRASP's schedule renderer. Your job is to write a concise, informative summary paragraph for a multi-course cooking schedule.

## DINNER CONCEPT
"{concept.free_text}"
- Meal type: {concept.meal_type.value}
- Occasion: {concept.occasion.value}
- Guest count: {concept.guest_count}

## RECIPES IN SCHEDULE
{", ".join(recipe_names)}

## SCHEDULED TIMELINE
{schedule_text}

## TOTAL DURATION
{merged_dag.total_duration_minutes} minutes ({merged_dag.total_duration_minutes // 60} hours {merged_dag.total_duration_minutes % 60} minutes){f" — worst case {merged_dag.total_duration_minutes_max} minutes" if merged_dag.total_duration_minutes_max else ""}

## ACTIVE TIME
{merged_dag.active_time_minutes} minutes of hands-on / active work (excludes passive steps like resting, chilling, braising).
{resource_warning_section}{error_section}
## OUTPUT REQUIREMENTS
1. `summary`: One paragraph (2-4 sentences) overview of the meal schedule. Include:
   - Number of courses and guest count
   - The anchor dish (longest duration) and how other prep fits around it
   - Total elapsed time and approximate active time
   - Mention any prep-ahead opportunities if present
   - If RESOURCE WARNINGS are present, mention the equipment constraints and any workarounds
2. {"`error_summary`: A single sentence about dropped recipes. Set to null if no errors." if has_errors else "`error_summary`: Set to null (no errors occurred)."}
3. Write for an experienced home cook. Be specific, not generic."""


# ── LLM factory (mockable seam) ─────────────────────────────────────────────


def _create_llm() -> ChatAnthropic:
    """
    Creates the ChatAnthropic instance. Extracted as a separate function so
    tests can patch graph.nodes.renderer._create_llm to bypass the real API.

    max_tokens=1024 is sufficient for a 2-4 sentence summary paragraph.
    Using a lower limit than the enricher (4096) keeps renderer cost low —
    the summary is a small output relative to the enrichment structured output.
    """
    settings = get_settings()
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",  # type: ignore[call-arg]
        api_key=settings.anthropic_api_key,
        max_tokens=1024,  # type: ignore[call-arg]
    )


# ── Fallback summary (used when LLM call fails) ─────────────────────────────


def _fallback_summary(merged_dag: MergedDAG, errors: list[dict]) -> str:
    """Generate a basic summary without LLM. Used on renderer failure.

    The fallback is deliberately minimal but correct — it includes recipe names
    and duration so the chef gets actionable information even without Claude's prose.
    This is a last resort: timeline is always returned, only the narrative fails.
    """
    recipe_names = sorted(set(s.recipe_name for s in merged_dag.scheduled_steps))
    hours = merged_dag.total_duration_minutes // 60
    minutes = merged_dag.total_duration_minutes % 60
    time_str = f"{hours} hours {minutes} minutes" if hours else f"{minutes} minutes"
    active_str = ""
    if merged_dag.active_time_minutes is not None:
        ah = merged_dag.active_time_minutes // 60
        am = merged_dag.active_time_minutes % 60
        active_str = f" Active time: {ah} hours {am} minutes." if ah else f" Active time: {am} minutes."
    return f"Schedule for {len(recipe_names)} course(s): {', '.join(recipe_names)}. Total elapsed time: {time_str}.{active_str}"


def _fallback_error_summary(errors: list[dict]) -> Optional[str]:
    """Generate a basic error summary without LLM.

    Extracts recipe_name from error metadata where available. If no specific
    recipe names are in the errors, falls back to a generic error count message.
    """
    if not errors:
        return None
    dropped = set()
    for err in errors:
        meta = err.get("metadata", {})
        name = meta.get("recipe_name")
        if name:
            dropped.add(name)
    if dropped:
        return f"Dropped recipe(s): {', '.join(sorted(dropped))}."
    return f"{len(errors)} recoverable error(s) occurred during pipeline execution."


# ── Node function ────────────────────────────────────────────────────────────


async def schedule_renderer_node(state: GRASPState) -> dict:
    """
    Real schedule renderer node. Converts MergedDAG to NaturalLanguageSchedule.

    Two phases:
      1. Deterministic timeline construction — always runs, no LLM.
         Produces the complete list of TimelineEntry objects.
      2. LLM summary generation — may fail; falls back to _fallback_summary().
         On failure, emits a recoverable error and returns the schedule with fallback text.

    The guard checks for missing merged_dag (should not happen — dag_merger failure
    is fatal and routes to error_router before reaching renderer) but defends
    against unexpected state corruption from checkpoint restore.

    errors from GRASPState are passed to the summary prompt so Claude can
    explain dropped recipes to the chef. They are NOT re-emitted here unless
    the renderer itself fails.
    """
    merged_dag_dict = state.get("merged_dag")
    errors = state.get("errors", [])

    if not merged_dag_dict:
        # Defensive guard — dag_merger failure routes to error_router, not here.
        # If we somehow reach renderer without a merged_dag, emit a fatal error.
        error = NodeError(
            node_name="schedule_renderer",
            error_type=ErrorType.UNKNOWN,
            recoverable=False,
            message="No merged_dag in state. Cannot render schedule.",
            metadata={},
        )
        return {"errors": [error.model_dump()]}

    try:
        # model_validate() re-parses the dict — required because state fields
        # come back as plain dicts after checkpoint restore, not Pydantic instances.
        merged_dag = MergedDAG.model_validate(merged_dag_dict)
    except Exception as exc:
        error = NodeError(
            node_name="schedule_renderer",
            error_type=ErrorType.VALIDATION_FAILURE,
            recoverable=False,
            message=f"Invalid merged_dag: {type(exc).__name__}: {exc}",
            metadata={"exception_type": type(exc).__name__},
        )
        return {"errors": [error.model_dump()]}

    # Read serving_time from concept — None means ASAP mode (T+0 labels).
    # serving_time is "HH:MM" 24-hour format, e.g. "19:00".
    concept_dict = state.get("concept", {})
    serving_time = concept_dict.get("serving_time")

    # Phase 1: Deterministic timeline construction.
    # This always succeeds — no external dependencies, no LLM.
    timeline = _build_timeline(merged_dag, serving_time)
    total_entries = len(timeline)

    # Phase 2: LLM summary generation (with fallback).
    try:
        concept = DinnerConcept.model_validate(state["concept"])  # type: ignore[typeddict-item]

        prompt = _build_summary_prompt(concept, merged_dag, errors)

        llm = _create_llm()
        chain = llm.with_structured_output(ScheduleSummaryOutput)

        @llm_retry
        async def _invoke_llm():
            return await chain.ainvoke(
                [
                    SystemMessage(content=prompt),
                    HumanMessage(
                        content=f"Write a summary for this {total_entries}-step "
                        f"cooking schedule ({merged_dag.total_duration_minutes} min total)."
                    ),
                ]
            )

        result = cast(ScheduleSummaryOutput, await _invoke_llm())

        summary = result.summary
        error_summary = result.error_summary
        usage = extract_token_usage(result, "schedule_renderer")

        logger.info(
            "Rendered schedule: %d entries (%d prep-ahead), %d min total",
            len(timeline),
            sum(1 for e in timeline if e.is_prep_ahead),
            merged_dag.total_duration_minutes,
        )

    except Exception as exc:
        # LLM failed — use fallback summary, emit recoverable error.
        # The schedule is still returned with the fallback text.
        # final_router will route this to mark_partial (PARTIAL status)
        # rather than COMPLETE, so the frontend can show an info message.
        logger.warning("Summary LLM failed (using fallback): %s", exc)

        summary = _fallback_summary(merged_dag, errors)
        error_summary = _fallback_error_summary(errors)

        render_error = NodeError(
            node_name="schedule_renderer",
            error_type=ErrorType.LLM_PARSE_FAILURE,
            recoverable=True,
            message=f"Summary generation failed: {type(exc).__name__}: {exc}",
            metadata={"exception_type": type(exc).__name__},
        )

        prep_ahead = [e for e in timeline if e.is_prep_ahead]

        schedule = NaturalLanguageSchedule(
            timeline=timeline,
            prep_ahead_entries=prep_ahead,
            total_duration_minutes=merged_dag.total_duration_minutes,
            total_duration_minutes_max=merged_dag.total_duration_minutes_max,
            active_time_minutes=merged_dag.active_time_minutes,
            summary=summary,
            error_summary=error_summary,
            one_oven_conflict=merged_dag.one_oven_conflict,
        )
        return {
            "schedule": schedule.model_dump(),
            "errors": [render_error.model_dump()],
        }

    # Happy path: LLM succeeded.
    # prep_ahead_entries is a pre-filtered copy of timeline for the frontend.
    prep_ahead = [e for e in timeline if e.is_prep_ahead]

    schedule = NaturalLanguageSchedule(
        timeline=timeline,
        prep_ahead_entries=prep_ahead,
        total_duration_minutes=merged_dag.total_duration_minutes,
        total_duration_minutes_max=merged_dag.total_duration_minutes_max,
        active_time_minutes=merged_dag.active_time_minutes,
        summary=summary,
        error_summary=error_summary,
        one_oven_conflict=merged_dag.one_oven_conflict,
    )
    return {
        "schedule": schedule.model_dump(),
        # token_usage uses operator.add accumulator — this list is APPENDED
        # to prior node usage (generator + enricher) in GRASPState.
        "token_usage": [usage],
    }
