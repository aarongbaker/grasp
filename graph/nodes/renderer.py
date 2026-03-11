"""
graph/nodes/renderer.py
Real schedule renderer — Phase 7. Final node in the pipeline.

Reads merged_dag + errors + concept from GRASPState, converts ScheduledStep
objects into TimelineEntry objects (deterministic), then calls Claude to
generate a natural-language summary and optional error_summary.

Error handling: renderer failure is RECOVERABLE (recoverable=True). A partial
schedule with a fallback summary is always better than no schedule. The
pipeline continues to final_router which routes to mark_partial.

IDEMPOTENCY: Returns schedule as a single dict (replace semantics).

Mockable seam:
  _create_llm()  — returns ChatAnthropic instance
"""

import logging
from typing import Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel

from models.pipeline import GRASPState, DinnerConcept
from models.scheduling import (
    MergedDAG, ScheduledStep, TimelineEntry, NaturalLanguageSchedule,
)
from models.enums import ErrorType, Resource
from models.errors import NodeError
from core.settings import get_settings

logger = logging.getLogger(__name__)


# ── Structured output wrapper ────────────────────────────────────────────────

class ScheduleSummaryOutput(BaseModel):
    """Wrapper for LangChain structured output. Claude returns this shape."""
    summary: str
    error_summary: Optional[str] = None


# ── Deterministic timeline construction ──────────────────────────────────────

def _build_timeline_entry(step: ScheduledStep) -> TimelineEntry:
    """Convert a ScheduledStep to a TimelineEntry. Pure, deterministic."""
    heads_up = None
    if step.duration_max and step.duration_max != step.duration_minutes:
        heads_up = (
            f"{step.duration_minutes}–{step.duration_max} min depending on oven"
        )

    return TimelineEntry(
        time_offset_minutes=step.start_at_minute,
        label=f"T+{step.start_at_minute}",
        step_id=step.step_id,
        recipe_name=step.recipe_name,
        action=step.description,
        resource=step.resource,
        duration_minutes=step.duration_minutes,
        duration_max=step.duration_max,
        heads_up=heads_up,
        is_prep_ahead=step.can_be_done_ahead,
        prep_ahead_window=step.prep_ahead_window,
    )


def _build_timeline(merged_dag: MergedDAG) -> list[TimelineEntry]:
    """Build the full timeline from a MergedDAG. Deterministic ordering."""
    return [_build_timeline_entry(step) for step in merged_dag.scheduled_steps]


# ── Prompt builders ──────────────────────────────────────────────────────────

def _format_schedule_for_prompt(merged_dag: MergedDAG) -> str:
    """Format the scheduled steps as a readable text block for the LLM."""
    lines = []
    for step in merged_dag.scheduled_steps:
        resource_label = step.resource.value.upper()
        line = (
            f"  T+{step.start_at_minute}: {step.recipe_name} — "
            f"{step.description} ({resource_label}, {step.duration_minutes} min)"
        )
        if step.can_be_done_ahead:
            line += f" [prep-ahead: {step.prep_ahead_window}]"
        lines.append(line)
    return "\n".join(lines)


def _format_errors_for_prompt(errors: list[dict]) -> str:
    """Format pipeline errors for the LLM to incorporate into error_summary."""
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
    """Build the system prompt for summary generation."""
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

    return f"""You are GRASP's schedule renderer. Your job is to write a concise, informative summary paragraph for a multi-course cooking schedule.

## DINNER CONCEPT
"{concept.free_text}"
- Meal type: {concept.meal_type.value}
- Occasion: {concept.occasion.value}
- Guest count: {concept.guest_count}

## RECIPES IN SCHEDULE
{', '.join(recipe_names)}

## SCHEDULED TIMELINE
{schedule_text}

## TOTAL DURATION
{merged_dag.total_duration_minutes} minutes ({merged_dag.total_duration_minutes // 60} hours {merged_dag.total_duration_minutes % 60} minutes)
{error_section}
## OUTPUT REQUIREMENTS
1. `summary`: One paragraph (2-4 sentences) overview of the meal schedule. Include:
   - Number of courses and guest count
   - The anchor dish (longest duration) and how other prep fits around it
   - Total elapsed time and approximate active time
   - Mention any prep-ahead opportunities if present
2. {"`error_summary`: A single sentence about dropped recipes. Set to null if no errors." if has_errors else "`error_summary`: Set to null (no errors occurred)."}
3. Write for an experienced home cook. Be specific, not generic."""


# ── LLM factory (mockable seam) ─────────────────────────────────────────────

def _create_llm() -> ChatAnthropic:
    """
    Creates the ChatAnthropic instance. Extracted as a separate function so
    tests can patch graph.nodes.renderer._create_llm to bypass the real API.
    """
    settings = get_settings()
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        api_key=settings.anthropic_api_key,
        max_tokens=1024,
    )


# ── Fallback summary (used when LLM call fails) ─────────────────────────────

def _fallback_summary(merged_dag: MergedDAG, errors: list[dict]) -> str:
    """Generate a basic summary without LLM. Used on renderer failure."""
    recipe_names = sorted(set(s.recipe_name for s in merged_dag.scheduled_steps))
    hours = merged_dag.total_duration_minutes // 60
    minutes = merged_dag.total_duration_minutes % 60
    time_str = f"{hours} hours {minutes} minutes" if hours else f"{minutes} minutes"
    return (
        f"Schedule for {len(recipe_names)} course(s): {', '.join(recipe_names)}. "
        f"Total elapsed time: {time_str}."
    )


def _fallback_error_summary(errors: list[dict]) -> Optional[str]:
    """Generate a basic error summary without LLM."""
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

    Timeline construction is deterministic (no LLM). Summary generation uses
    Claude. On LLM failure, falls back to a basic summary (recoverable error).
    """
    merged_dag_dict = state.get("merged_dag")
    errors = state.get("errors", [])

    if not merged_dag_dict:
        # No merged DAG — this shouldn't happen (dag_merger failure is fatal),
        # but handle defensively.
        error = NodeError(
            node_name="schedule_renderer",
            error_type=ErrorType.UNKNOWN,
            recoverable=False,
            message="No merged_dag in state. Cannot render schedule.",
            metadata={},
        )
        return {"errors": [error.model_dump()]}

    try:
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

    # Deterministic timeline construction
    timeline = _build_timeline(merged_dag)

    # LLM summary generation (with fallback)
    try:
        concept = DinnerConcept.model_validate(state["concept"])

        prompt = _build_summary_prompt(concept, merged_dag, errors)

        llm = _create_llm()
        chain = llm.with_structured_output(ScheduleSummaryOutput)

        result = await chain.ainvoke([
            SystemMessage(content=prompt),
            HumanMessage(
                content=f"Write a summary for this {len(timeline)}-step "
                f"cooking schedule ({merged_dag.total_duration_minutes} min total)."
            ),
        ])

        summary = result.summary
        error_summary = result.error_summary

        logger.info(
            "Rendered schedule: %d timeline entries, %d min total",
            len(timeline),
            merged_dag.total_duration_minutes,
        )

    except Exception as exc:
        # LLM failed — use fallback summary, emit recoverable error
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

        schedule = NaturalLanguageSchedule(
            timeline=timeline,
            total_duration_minutes=merged_dag.total_duration_minutes,
            summary=summary,
            error_summary=error_summary,
        )
        return {
            "schedule": schedule.model_dump(),
            "errors": [render_error.model_dump()],
        }

    schedule = NaturalLanguageSchedule(
        timeline=timeline,
        total_duration_minutes=merged_dag.total_duration_minutes,
        summary=summary,
        error_summary=error_summary,
    )
    return {"schedule": schedule.model_dump()}
