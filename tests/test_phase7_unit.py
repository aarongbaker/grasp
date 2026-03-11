"""
tests/test_phase7_unit.py
Unit tests for Phase 7: Schedule Renderer.

These tests call the renderer's internal functions directly — no graph,
no database, no LLM. They verify:
  - Timeline construction: ScheduledStep → TimelineEntry (deterministic)
  - Fallback summary generation (no LLM)
  - Full node function with mocked LLM
  - Error handling: missing merged_dag, LLM failure (recoverable)
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from models.pipeline import DinnerConcept
from models.scheduling import (
    MergedDAG, ScheduledStep, TimelineEntry, NaturalLanguageSchedule,
)
from models.enums import Resource, MealType, Occasion, ErrorType

from graph.nodes.renderer import (
    _build_timeline_entry,
    _build_timeline,
    _fallback_summary,
    _fallback_error_summary,
    _build_summary_prompt,
    ScheduleSummaryOutput,
    schedule_renderer_node,
)

from tests.fixtures.schedules import (
    MERGED_DAG_FULL,
    MERGED_DAG_TWO_RECIPE,
    NATURAL_LANGUAGE_SCHEDULE_FULL,
    NATURAL_LANGUAGE_SCHEDULE_TWO_RECIPE,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

CONCEPT_DICT = DinnerConcept(
    free_text="A special dinner party with short ribs, potato puree, and chocolate fondant.",
    guest_count=4,
    meal_type=MealType.DINNER,
    occasion=Occasion.DINNER_PARTY,
    dietary_restrictions=[],
).model_dump()

KITCHEN_CONFIG = {
    "max_burners": 4,
    "max_oven_racks": 2,
    "has_second_oven": False,
}


def _make_state(merged_dag=None, errors=None, concept=None):
    """Build a minimal GRASPState dict for renderer tests."""
    return {
        "concept": concept or CONCEPT_DICT,
        "kitchen_config": KITCHEN_CONFIG,
        "merged_dag": merged_dag,
        "errors": errors or [],
    }


# ── Timeline Entry Construction ──────────────────────────────────────────────

class TestBuildTimelineEntry:
    def test_basic_step(self):
        """A simple step with no duration_max produces no heads_up."""
        step = ScheduledStep(
            step_id="test_step_1",
            recipe_name="Test Recipe",
            description="Do something.",
            resource=Resource.HANDS,
            duration_minutes=10,
            start_at_minute=0,
            end_at_minute=10,
        )
        entry = _build_timeline_entry(step)

        assert entry.time_offset_minutes == 0
        assert entry.label == "T+0"
        assert entry.step_id == "test_step_1"
        assert entry.recipe_name == "Test Recipe"
        assert entry.action == "Do something."
        assert entry.resource == Resource.HANDS
        assert entry.duration_minutes == 10
        assert entry.heads_up is None
        assert entry.is_prep_ahead is False
        assert entry.prep_ahead_window is None

    def test_variable_duration_heads_up(self):
        """Step with duration_max != duration_minutes produces heads_up."""
        step = ScheduledStep(
            step_id="bake_1",
            recipe_name="Fondant",
            description="Bake at 200°C.",
            resource=Resource.OVEN,
            duration_minutes=12,
            duration_max=14,
            start_at_minute=180,
            end_at_minute=192,
        )
        entry = _build_timeline_entry(step)

        assert entry.heads_up == "12–14 min depending on oven"
        assert entry.duration_max == 14

    def test_same_duration_no_heads_up(self):
        """Step with duration_max == duration_minutes produces no heads_up."""
        step = ScheduledStep(
            step_id="boil_1",
            recipe_name="Pasta",
            description="Boil pasta.",
            resource=Resource.STOVETOP,
            duration_minutes=10,
            duration_max=10,
            start_at_minute=0,
            end_at_minute=10,
        )
        entry = _build_timeline_entry(step)
        assert entry.heads_up is None

    def test_prep_ahead_step(self):
        """Prep-ahead step flags are preserved."""
        step = ScheduledStep(
            step_id="chill_1",
            recipe_name="Fondant",
            description="Chill ramekins.",
            resource=Resource.PASSIVE,
            duration_minutes=30,
            start_at_minute=55,
            end_at_minute=85,
            can_be_done_ahead=True,
            prep_ahead_window="up to 24 hours",
        )
        entry = _build_timeline_entry(step)

        assert entry.is_prep_ahead is True
        assert entry.prep_ahead_window == "up to 24 hours"

    def test_label_format(self):
        """Label is T+{start_at_minute}."""
        step = ScheduledStep(
            step_id="s1", recipe_name="R", description="D",
            resource=Resource.HANDS, duration_minutes=5,
            start_at_minute=45, end_at_minute=50,
        )
        entry = _build_timeline_entry(step)
        assert entry.label == "T+45"


class TestBuildTimeline:
    def test_full_timeline_length(self):
        """Full 3-recipe merged DAG produces 12 timeline entries."""
        timeline = _build_timeline(MERGED_DAG_FULL)
        assert len(timeline) == 12

    def test_two_recipe_timeline_length(self):
        """2-recipe merged DAG produces 7 timeline entries."""
        timeline = _build_timeline(MERGED_DAG_TWO_RECIPE)
        assert len(timeline) == 7

    def test_timeline_ordering_preserved(self):
        """Timeline entries match MergedDAG step order (by start_at_minute)."""
        timeline = _build_timeline(MERGED_DAG_FULL)
        offsets = [e.time_offset_minutes for e in timeline]
        assert offsets == sorted(offsets)

    def test_fixture_timeline_matches(self):
        """Our deterministic _build_timeline matches the fixture timeline."""
        timeline = _build_timeline(MERGED_DAG_FULL)
        fixture_timeline = NATURAL_LANGUAGE_SCHEDULE_FULL.timeline

        assert len(timeline) == len(fixture_timeline)
        for built, fixture in zip(timeline, fixture_timeline):
            assert built.step_id == fixture.step_id
            assert built.time_offset_minutes == fixture.time_offset_minutes
            assert built.resource == fixture.resource
            assert built.recipe_name == fixture.recipe_name


# ── Fallback Summary ────────────────────────────────────────────────────────

class TestFallbackSummary:
    def test_full_schedule_summary(self):
        """Fallback summary mentions all recipe names and total time."""
        summary = _fallback_summary(MERGED_DAG_FULL, [])
        assert "3 course(s)" in summary
        assert "Braised Short Ribs" in summary
        assert "Chocolate Fondant" in summary
        assert "Pommes Puree" in summary
        assert "3 hours 15 minutes" in summary

    def test_two_recipe_summary(self):
        """Fallback summary for 2-recipe schedule."""
        summary = _fallback_summary(MERGED_DAG_TWO_RECIPE, [])
        assert "2 course(s)" in summary
        assert "Braised Short Ribs" in summary
        assert "Pommes Puree" in summary

    def test_fallback_error_summary_no_errors(self):
        """No errors returns None."""
        assert _fallback_error_summary([]) is None

    def test_fallback_error_summary_with_recipe_name(self):
        """Error with recipe_name metadata mentions the dropped recipe."""
        errors = [{
            "node_name": "rag_enricher",
            "error_type": "rag_failure",
            "recoverable": True,
            "message": "Failed",
            "metadata": {"recipe_name": "Chocolate Fondant"},
        }]
        summary = _fallback_error_summary(errors)
        assert "Chocolate Fondant" in summary

    def test_fallback_error_summary_no_recipe_name(self):
        """Error without recipe_name metadata gives generic message."""
        errors = [{
            "node_name": "validator",
            "error_type": "validation_failure",
            "recoverable": True,
            "message": "Failed",
            "metadata": {},
        }]
        summary = _fallback_error_summary(errors)
        assert "1 recoverable error" in summary


# ── Prompt Builder ──────────────────────────────────────────────────────────

class TestBuildSummaryPrompt:
    def test_prompt_includes_concept(self):
        """Prompt includes dinner concept text."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        prompt = _build_summary_prompt(concept, MERGED_DAG_FULL, [])
        assert "short ribs" in prompt
        assert "dinner_party" in prompt
        assert "Guest count: 4" in prompt

    def test_prompt_includes_recipe_names(self):
        """Prompt includes all recipe names from the schedule."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        prompt = _build_summary_prompt(concept, MERGED_DAG_FULL, [])
        assert "Braised Short Ribs" in prompt
        assert "Chocolate Fondant" in prompt
        assert "Pommes Puree" in prompt

    def test_prompt_includes_total_duration(self):
        """Prompt includes total duration."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        prompt = _build_summary_prompt(concept, MERGED_DAG_FULL, [])
        assert "195 minutes" in prompt

    def test_prompt_with_errors(self):
        """Prompt with errors includes error section and instructions."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        errors = [{
            "node_name": "rag_enricher",
            "message": "Fondant enrichment failed",
            "metadata": {"recipe_name": "Chocolate Fondant"},
        }]
        prompt = _build_summary_prompt(concept, MERGED_DAG_TWO_RECIPE, errors)
        assert "PIPELINE ERRORS" in prompt
        assert "Fondant enrichment failed" in prompt
        assert "error_summary" in prompt

    def test_prompt_without_errors(self):
        """Prompt without errors tells LLM to set error_summary to null."""
        concept = DinnerConcept.model_validate(CONCEPT_DICT)
        prompt = _build_summary_prompt(concept, MERGED_DAG_FULL, [])
        assert "Set to null (no errors occurred)" in prompt


# ── Node Function ───────────────────────────────────────────────────────────

class TestScheduleRendererNode:
    @pytest.mark.asyncio
    async def test_happy_path_full(self):
        """Full 3-recipe schedule with mocked LLM produces valid NaturalLanguageSchedule."""
        mock_output = ScheduleSummaryOutput(
            summary="A three-course dinner party menu.",
            error_summary=None,
        )
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = mock_output
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        state = _make_state(
            merged_dag=MERGED_DAG_FULL.model_dump(),
        )

        with patch("graph.nodes.renderer._create_llm", return_value=mock_llm):
            result = await schedule_renderer_node(state)

        assert "schedule" in result
        assert "errors" not in result

        schedule = NaturalLanguageSchedule.model_validate(result["schedule"])
        assert len(schedule.timeline) == 12
        assert schedule.total_duration_minutes == 195
        assert schedule.summary == "A three-course dinner party menu."
        assert schedule.error_summary is None

    @pytest.mark.asyncio
    async def test_happy_path_with_errors(self):
        """2-recipe schedule (partial) with errors populates error_summary."""
        mock_output = ScheduleSummaryOutput(
            summary="A two-course dinner.",
            error_summary="Chocolate Fondant dropped due to enrichment failure.",
        )
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = mock_output
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        errors = [{
            "node_name": "rag_enricher",
            "error_type": "rag_failure",
            "recoverable": True,
            "message": "Enrichment failed for 'Chocolate Fondant'",
            "metadata": {"recipe_name": "Chocolate Fondant"},
        }]
        state = _make_state(
            merged_dag=MERGED_DAG_TWO_RECIPE.model_dump(),
            errors=errors,
        )

        with patch("graph.nodes.renderer._create_llm", return_value=mock_llm):
            result = await schedule_renderer_node(state)

        schedule = NaturalLanguageSchedule.model_validate(result["schedule"])
        assert len(schedule.timeline) == 7
        assert schedule.error_summary == "Chocolate Fondant dropped due to enrichment failure."

    @pytest.mark.asyncio
    async def test_no_merged_dag_fatal(self):
        """Missing merged_dag returns fatal error (shouldn't happen in practice)."""
        state = _make_state(merged_dag=None)
        result = await schedule_renderer_node(state)

        assert "errors" in result
        assert "schedule" not in result
        error = result["errors"][0]
        assert error["node_name"] == "schedule_renderer"
        assert error["recoverable"] is False

    @pytest.mark.asyncio
    async def test_llm_failure_recoverable(self):
        """LLM failure produces schedule with fallback summary + recoverable error."""
        mock_chain = AsyncMock()
        mock_chain.ainvoke.side_effect = Exception("API timeout")
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        state = _make_state(
            merged_dag=MERGED_DAG_FULL.model_dump(),
        )

        with patch("graph.nodes.renderer._create_llm", return_value=mock_llm):
            result = await schedule_renderer_node(state)

        # Should still have a schedule (with fallback summary)
        assert "schedule" in result
        schedule = NaturalLanguageSchedule.model_validate(result["schedule"])
        assert len(schedule.timeline) == 12
        assert "3 course(s)" in schedule.summary

        # Should have a recoverable error
        assert "errors" in result
        error = result["errors"][0]
        assert error["node_name"] == "schedule_renderer"
        assert error["error_type"] == ErrorType.LLM_PARSE_FAILURE.value
        assert error["recoverable"] is True

    @pytest.mark.asyncio
    async def test_llm_failure_with_existing_errors(self):
        """LLM failure with pre-existing errors uses fallback error_summary."""
        mock_chain = AsyncMock()
        mock_chain.ainvoke.side_effect = Exception("API timeout")
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        errors = [{
            "node_name": "rag_enricher",
            "error_type": "rag_failure",
            "recoverable": True,
            "message": "Failed",
            "metadata": {"recipe_name": "Chocolate Fondant"},
        }]
        state = _make_state(
            merged_dag=MERGED_DAG_TWO_RECIPE.model_dump(),
            errors=errors,
        )

        with patch("graph.nodes.renderer._create_llm", return_value=mock_llm):
            result = await schedule_renderer_node(state)

        schedule = NaturalLanguageSchedule.model_validate(result["schedule"])
        assert "Chocolate Fondant" in schedule.error_summary

    @pytest.mark.asyncio
    async def test_invalid_merged_dag_fatal(self):
        """Corrupted merged_dag dict returns fatal error."""
        state = _make_state(merged_dag={"not_valid": True})
        result = await schedule_renderer_node(state)

        assert "errors" in result
        assert "schedule" not in result
        error = result["errors"][0]
        assert error["recoverable"] is False
        assert error["error_type"] == ErrorType.VALIDATION_FAILURE.value

    @pytest.mark.asyncio
    async def test_timeline_determinism(self):
        """Timeline entries are identical across multiple calls (no LLM involved)."""
        mock_output = ScheduleSummaryOutput(summary="Test.", error_summary=None)
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = mock_output
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain

        state = _make_state(merged_dag=MERGED_DAG_FULL.model_dump())

        with patch("graph.nodes.renderer._create_llm", return_value=mock_llm):
            result1 = await schedule_renderer_node(state)
            result2 = await schedule_renderer_node(state)

        s1 = NaturalLanguageSchedule.model_validate(result1["schedule"])
        s2 = NaturalLanguageSchedule.model_validate(result2["schedule"])

        for e1, e2 in zip(s1.timeline, s2.timeline):
            assert e1.step_id == e2.step_id
            assert e1.time_offset_minutes == e2.time_offset_minutes
            assert e1.action == e2.action
