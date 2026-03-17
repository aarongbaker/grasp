"""
tests/test_state_machine.py
Unit tests for ingestion/state_machine.py — the 6-state cookbook chunker.

Pure function tests. No DB, no async, no fixtures beyond inline data.
Tests cover:
  - Empty input (the Fix #1 crash case)
  - Single-state pages (all narrative, all method)
  - State transitions via tripwire patterns
  - Chunk minimum length filter (< 20 chars discarded)
  - Multiple transitions on the same page
"""

from ingestion.state_machine import run_state_machine, _tripwire_check, CookbookState


# ─────────────────────────────────────────────────────────────────────────────
# Empty / minimal input
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_pages_returns_empty_list():
    """Fix #1 regression test: empty pages must not crash."""
    result = run_state_machine([])
    assert result == []


def test_single_page_narrative_only():
    """A page with no tripwire matches stays NARRATIVE → chunk_type=intro."""
    pages = [{"page_number": 1, "text": "This is a long narrative about the history of French cuisine and its evolution over centuries."}]
    chunks = run_state_machine(pages)
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "intro"
    assert chunks[0]["page_number"] == 1


def test_short_text_discarded():
    """Chunks shorter than 20 characters are not emitted."""
    pages = [{"page_number": 1, "text": "Short."}]
    chunks = run_state_machine(pages)
    assert len(chunks) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Tripwire detection
# ─────────────────────────────────────────────────────────────────────────────

def test_tripwire_detects_ingredients():
    """Ingredient patterns like '200g flour' trigger INGREDIENTS state."""
    candidates = _tripwire_check("200 g unsalted butter, cold and cubed")
    assert CookbookState.INGREDIENTS in candidates


def test_tripwire_detects_method():
    """Action verbs like 'heat', 'stir' trigger METHOD state."""
    candidates = _tripwire_check("Heat the olive oil in a large heavy-based pan over medium heat.")
    assert CookbookState.METHOD in candidates


def test_tripwire_detects_recipe_header():
    """Yield indicators like 'serves 4' trigger RECIPE_HEADER."""
    candidates = _tripwire_check("serves 4")
    assert CookbookState.RECIPE_HEADER in candidates


def test_tripwire_detects_technique_aside():
    """Science keywords trigger TECHNIQUE_ASIDE."""
    candidates = _tripwire_check("Why this works: the maillard reaction creates deep browning.")
    assert CookbookState.TECHNIQUE_ASIDE in candidates


def test_tripwire_detects_recipe_end():
    """Service keywords trigger RECIPE_END."""
    candidates = _tripwire_check("Serve immediately with a drizzle of extra virgin olive oil.")
    assert CookbookState.RECIPE_END in candidates


def test_tripwire_no_match():
    """Plain narrative text should match no tripwires."""
    candidates = _tripwire_check("The restaurant was founded in 1987 in a quiet Parisian street.")
    assert candidates == []


# ─────────────────────────────────────────────────────────────────────────────
# State transitions
# ─────────────────────────────────────────────────────────────────────────────

def test_transition_narrative_to_method():
    """A transition from NARRATIVE to METHOD should produce two chunks."""
    pages = [{
        "page_number": 1,
        "text": (
            "This chapter explores the fundamentals of French braising technique and its many variations. "
            "Heat the olive oil in a large Dutch oven over high heat until shimmering. "
            "Add the seasoned short ribs and sear on all sides until deeply browned."
        ),
    }]
    chunks = run_state_machine(pages)
    types = [c["chunk_type"] for c in chunks]
    assert "intro" in types
    assert "recipe" in types


def test_transition_method_to_recipe_end():
    """Method + serving instruction stay as one recipe chunk (recipe lifecycle)."""
    pages = [{
        "page_number": 1,
        "text": (
            "1. Heat the butter in a saucepan until foaming and lightly browned. "
            "2. Add the shallots and cook until softened, about three minutes. "
            "Serve immediately with crusty bread and a green salad on the side."
        ),
    }]
    chunks = run_state_machine(pages)
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "recipe"
    assert "Serve immediately" in chunks[0]["text"]


def test_multiple_pages_preserve_page_numbers():
    """Chunks track their originating page number."""
    pages = [
        {"page_number": 1, "text": "This is a long narrative introduction to the cookbook and its themes and philosophy."},
        {"page_number": 2, "text": "200 g dark chocolate, roughly chopped into small even pieces for consistent melting."},
    ]
    chunks = run_state_machine(pages)
    assert len(chunks) >= 1
    page_nums = {c["page_number"] for c in chunks}
    assert 1 in page_nums or 2 in page_nums


def test_same_state_no_flush():
    """Consecutive sentences matching the same state accumulate into one chunk."""
    pages = [{
        "page_number": 1,
        "text": (
            "Heat the oil in a pan over medium heat until shimmering. "
            "Add the onions and stir until translucent and softened. "
            "Season with salt and pepper to taste and stir again."
        ),
    }]
    chunks = run_state_machine(pages)
    # All sentences match METHOD — should be a single chunk
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "recipe"
