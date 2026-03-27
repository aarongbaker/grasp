"""
tests/test_state_machine.py
Unit tests for ingestion/state_machine.py — the 6-state cookbook chunker.

Pure function tests. No DB, no async, no fixtures beyond inline data.
Tests cover:
  - Empty input (the Fix #1 crash case)
  - Single-state pages (all narrative, all method)
  - Tripwire detection
  - Chunk minimum length filter (< 20 chars discarded)
  - Multiple transitions on the same page
  - OCR-heavy historical cookbook line structure
"""

from pathlib import Path

import fitz

from app.ingestion.state_machine import CookbookState, _tripwire_check, run_state_machine

# ─────────────────────────────────────────────────────────────────────────────
# Empty / minimal input
# ─────────────────────────────────────────────────────────────────────────────


def test_empty_pages_returns_empty_list():
    """Fix #1 regression test: empty pages must not crash."""
    result = run_state_machine([])
    assert result == []


def test_single_page_narrative_only():
    """A page with no tripwire matches stays NARRATIVE → chunk_type=intro."""
    pages = [
        {
            "page_number": 1,
            "text": "This is a long narrative about the history of French cuisine and its evolution over centuries.",
        }
    ]
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
    """Method-only prose inside a narrative paragraph should not start a recipe chunk by itself."""
    pages = [
        {
            "page_number": 1,
            "text": (
                "This chapter explores the fundamentals of French braising technique and its many variations. "
                "Heat the olive oil in a large Dutch oven over high heat until shimmering. "
                "Add the seasoned short ribs and sear on all sides until deeply browned."
            ),
        }
    ]
    chunks = run_state_machine(pages)
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "intro"


def test_transition_method_to_recipe_end():
    """Method + serving instruction stay as one recipe chunk (recipe lifecycle)."""
    pages = [
        {
            "page_number": 1,
            "text": (
                "1. Heat the butter in a saucepan until foaming and lightly browned. "
                "2. Add the shallots and cook until softened, about three minutes. "
                "Serve immediately with crusty bread and a green salad on the side."
            ),
        }
    ]
    chunks = run_state_machine(pages)
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "recipe"
    assert "Serve immediately" in chunks[0]["text"]


def test_multiple_pages_preserve_page_numbers():
    """Chunks track their originating page number."""
    pages = [
        {
            "page_number": 1,
            "text": "This is a long narrative introduction to the cookbook and its themes and philosophy.",
        },
        {
            "page_number": 2,
            "text": "200 g dark chocolate, roughly chopped into small even pieces for consistent melting.",
        },
    ]
    chunks = run_state_machine(pages)
    assert len(chunks) >= 1
    page_nums = {c["page_number"] for c in chunks}
    assert 1 in page_nums or 2 in page_nums


def test_same_state_no_flush():
    """Consecutive sentences matching the same state accumulate into one chunk."""
    pages = [
        {
            "page_number": 1,
            "text": (
                "Heat the oil in a pan over medium heat until shimmering. "
                "Add the onions and stir until translucent and softened. "
                "Season with salt and pepper to taste and stir again."
            ),
        }
    ]
    chunks = run_state_machine(pages)
    # All sentences match METHOD — should be a single chunk
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "recipe"


def test_header_detection_rejects_front_matter_and_all_caps_noise():
    """Front matter and all-caps marketing copy should not be treated as recipe headers."""
    pages = [
        {
            "page_number": 1,
            "text": (
                'DELICIOUS RECIPES\n'
                'THAT HAVE MADE SOUTHERN COOKING FAMOUS THE WORLD\n'
                'OVER.\n'
                'Charlotte, N. C., News.\n'
                'This is a long narrative introduction to the cookbook and its history.'
            ),
        }
    ]

    chunks = run_state_machine(pages)

    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "intro"
    assert "DELICIOUS RECIPES" in chunks[0]["text"]


def test_ocr_split_title_lines_merge_back_into_single_recipe_header():
    """A title followed by ingredient-style content should stay attached to one recipe chunk."""
    pages = [
        {
            "page_number": 20,
            "text": (
                "Shrimp Paste\n"
                "2 cups boiled shrimp\n"
                "Run a quart of boiled and picked shrimp through the meat grinder.\n"
                "Put in a saucepan with salt, pepper, mace and two heaping tablespoons of butter.\n"
                "Heat thoroughly and place into molds."
            ),
        }
    ]

    chunks = run_state_machine(pages)
    recipe_chunks = [c for c in chunks if c["chunk_type"] == "recipe"]

    assert len(recipe_chunks) == 1
    assert "Shrimp Paste" in recipe_chunks[0]["text"]


def test_ocr_heavy_page_with_multiple_recipes_splits_into_multiple_recipe_chunks():
    """Historical cookbook OCR should preserve multiple recipe candidates from a dense page."""
    page_text = """18
THE SOUTHERN COOK BOOK
Shrimp Sauce
(To Be Served with Fish)
1V2 cups chopped
cooked
shrimps
3 tablespoons
lemon
juice
salt and pepper to
taste
iVz cups white sauce
(see page 24)
2 hard cooked eggs
Soak shrimps
in lemon
juice
one-half hour
and add them
to white sauce; when ready
to
serve add the finely chopped hard cooked egg
and
a
little minced
parsley.
Pour
this
over
the
fish.
Deviled Crabs Norfolk
Make
a
white
sauce
by mixing
one
table-
spoonful
of melted butter and one tablespoon-
ful
of
flour; add one-half
cup
of
cream
or
milk and
let come to a
boil, stirring constant-
ly.
Add
salt and pepper.
Then add one pint
of crab meat, two chopped hard cooked eggs,
sprig of
parsley, dash of Worcestershire sauce
and
place
in
the
shells.
Brush
with
melted
butter and cracker crumbs and bake
in slow
oven
until
well browned.
Crab Croquettes
2 cups crab meat
1
teaspoon onion juice
salt and pepper
chopped parsley
1 cup white sauce
(see page 24)
cracker crumbs
1
egg, beaten
Chop the crab meat fine and add the season-
ings. When well mixed, add to the white sauce.
Mold
into
croquettes,
roll
in
cracker crumbs,
dip
in
the
slightly
beaten
egg, and then
roll
in the crumbs again. Fry
in deep hot
fat until
golden
brown.
"""
    chunks = run_state_machine([{"page_number": 18, "text": page_text}])
    recipe_chunks = [c for c in chunks if c["chunk_type"] == "recipe"]

    assert len(recipe_chunks) >= 2
    assert any("Shrimp Sauce" in c["text"] for c in recipe_chunks)
    assert any("Crab Croquettes" in c["text"] for c in recipe_chunks)


def test_southern_cookbook_pdf_yields_more_than_collapsed_single_recipe_result():
    """Regression check against the real OCR-heavy PDF that previously collapsed to one recipe chunk."""
    pdf_path = Path("/Users/aaronbaker/Desktop/cookbooks/southerncookbook00lustrich.pdf")
    if not pdf_path.exists():
        return

    doc = fitz.open(str(pdf_path))
    pages = [{"page_number": i + 1, "text": doc[i].get_text("text") or ""} for i in range(len(doc))]
    chunks = run_state_machine(pages)
    recipe_chunks = [c for c in chunks if c["chunk_type"] == "recipe"]

    assert len(recipe_chunks) > 16


def test_recipe_state_flushes_when_new_page_starts_with_front_matter_noise():
    """A recipe chunk should not absorb the next page's index/front-matter text just because no new header is detected."""
    pages = [
        {
            "page_number": 51,
            "text": (
                "Shrimp Paste\n"
                "2 cups boiled shrimp\n"
                "Run a quart of boiled and picked shrimp through the meat grinder.\n"
                "Put in a saucepan with salt, pepper, mace and butter.\n"
                "Heat thoroughly and place into molds."
            ),
        },
        {
            "page_number": 52,
            "text": (
                "INDEX\n"
                "APPETIZERS\n"
                "Avocado Canapes 47\n"
                "Cheese Appetizer 47\n"
                "Introduction\n"
                "PEOPLE think of the Southland as the place where the sun shines brighter."
            ),
        },
    ]

    chunks = run_state_machine(pages)
    shrimp_chunk = next(c for c in chunks if c["chunk_type"] == "recipe" and "Shrimp Paste" in c["text"])

    assert shrimp_chunk["page_number"] == 51
    assert "INDEX" not in shrimp_chunk["text"]
    assert "Introduction" not in shrimp_chunk["text"]
    assert any(c["page_number"] == 52 for c in chunks if c["chunk_type"] in {"recipe", "intro"})


def test_recipe_chunk_page_number_stays_at_recipe_start_page_across_continuation_pages():
    """Multi-page recipes should preserve the originating page number instead of drifting to the final page."""
    pages = [
        {
            "page_number": 10,
            "text": (
                "Chicken Gumbo\n"
                "1 small stewing chicken\n"
                "2 tablespoons flour\n"
                "After chicken is cleaned and dressed, cut it into serving portions."
            ),
        },
        {
            "page_number": 11,
            "text": (
                "When the chicken is nicely browned, add the okra, tomatoes, parsley and water.\n"
                "Season to taste with salt and pepper.\n"
                "Cook very slowly until the chicken is tender."
            ),
        },
    ]

    chunks = run_state_machine(pages)
    recipe_chunks = [c for c in chunks if c["chunk_type"] == "recipe"]

    assert len(recipe_chunks) == 1
    assert recipe_chunks[0]["page_number"] == 10
    assert "Chicken Gumbo" in recipe_chunks[0]["text"]
    assert "Cook very slowly" in recipe_chunks[0]["text"]
