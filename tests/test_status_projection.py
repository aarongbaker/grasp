"""
tests/test_status_projection.py
Unit tests for core/status.py — status_projection() derivation logic.

Tests each pipeline stage by mocking graph.aget_state() to return
controlled state snapshots. No real LangGraph graph needed.

Covers all 5 derivation rules:
  empty state         → GENERATING
  raw_recipes         → ENRICHING
  enriched_recipes    → VALIDATING
  validated_recipes   → SCHEDULING
  recipe_dags         → SCHEDULING
  merged_dag          → SCHEDULING
  exception           → GENERATING (safe fallback)
"""

import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest
from core.status import status_projection
from models.enums import SessionStatus


def _make_mock_graph(state_values: dict):
    """Create a mock graph whose aget_state returns a snapshot with given values."""
    graph = AsyncMock()
    snapshot = MagicMock()
    snapshot.values = state_values
    graph.aget_state.return_value = snapshot
    return graph


def _make_mock_graph_none():
    """Mock graph that returns None from aget_state (no checkpoint)."""
    graph = AsyncMock()
    graph.aget_state.return_value = None
    return graph


def _make_mock_graph_error():
    """Mock graph that raises when aget_state is called."""
    graph = AsyncMock()
    graph.aget_state.side_effect = Exception("checkpoint unavailable")
    return graph


# ─────────────────────────────────────────────────────────────────────────────
# Derivation rules
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_state_returns_generating():
    graph = _make_mock_graph({})
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.GENERATING


@pytest.mark.asyncio
async def test_none_snapshot_returns_generating():
    graph = _make_mock_graph_none()
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.GENERATING


@pytest.mark.asyncio
async def test_raw_recipes_returns_enriching():
    """raw_recipes populated means generator ran → enricher is next."""
    graph = _make_mock_graph({"raw_recipes": [{"name": "test"}]})
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.ENRICHING


@pytest.mark.asyncio
async def test_enriched_recipes_returns_validating():
    """enriched_recipes populated means enricher ran → validator is next."""
    graph = _make_mock_graph({
        "raw_recipes": [{"name": "test"}],
        "enriched_recipes": [{"source": {}, "steps": []}],
    })
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.VALIDATING


@pytest.mark.asyncio
async def test_validated_recipes_returns_scheduling():
    """validated_recipes populated → SCHEDULING (dag_builder is next)."""
    graph = _make_mock_graph({
        "raw_recipes": [{"name": "test"}],
        "enriched_recipes": [{"source": {}, "steps": []}],
        "validated_recipes": [{"source": {}, "validated_at": "2024-01-01"}],
    })
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.SCHEDULING


@pytest.mark.asyncio
async def test_recipe_dags_returns_scheduling():
    """recipe_dags populated → SCHEDULING (dag_merger is next)."""
    graph = _make_mock_graph({
        "raw_recipes": [{"name": "test"}],
        "enriched_recipes": [{"source": {}, "steps": []}],
        "validated_recipes": [{"source": {}, "validated_at": "2024-01-01"}],
        "recipe_dags": [{"recipe_name": "test", "edges": []}],
    })
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.SCHEDULING


@pytest.mark.asyncio
async def test_merged_dag_returns_scheduling():
    """merged_dag populated → SCHEDULING (renderer is next)."""
    graph = _make_mock_graph({
        "raw_recipes": [{"name": "test"}],
        "merged_dag": {"scheduled_steps": [], "total_duration_minutes": 100},
    })
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.SCHEDULING


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases / fallbacks
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exception_returns_generating():
    """If graph.aget_state raises, safe fallback is GENERATING."""
    graph = _make_mock_graph_error()
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.GENERATING


@pytest.mark.asyncio
async def test_empty_lists_treated_as_not_populated():
    """Empty lists are falsy — should not advance status."""
    graph = _make_mock_graph({
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
    })
    status = await status_projection(uuid.uuid4(), graph)
    assert status == SessionStatus.GENERATING


@pytest.mark.asyncio
async def test_most_advanced_field_wins():
    """When multiple fields populated, the most advanced one determines status."""
    graph = _make_mock_graph({
        "raw_recipes": [{"name": "a"}],
        "enriched_recipes": [{"source": {}}],
        "validated_recipes": [{"source": {}}],
        "merged_dag": {"scheduled_steps": []},
    })
    status = await status_projection(uuid.uuid4(), graph)
    # merged_dag is most advanced → SCHEDULING (not VALIDATING or ENRICHING)
    assert status == SessionStatus.SCHEDULING
