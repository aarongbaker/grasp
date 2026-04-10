"""
ingestion/state_machine.py

INTERNAL INFRASTRUCTURE ONLY — Phase 2c: Cookbook state machine — 6 states.

After M015 pivot (cookbook de-scope), this module is used only for team/admin
curated cookbook uploads, not user-facing upload flows.

Two-tier transition detection: regex tripwire (every sentence, free) →
LLM detector only on candidates (~5-10% of sentences).

Chunking philosophy:
  - A complete recipe (header → ingredients → method → end) is ONE chunk.
    Splitting a recipe across chunks destroys RAG retrieval quality — a
    retrieval query for "braise short ribs" needs the full method, not half
    of it. Recipe chunks have no word limit.
  - Narrative, technique asides, and other non-recipe content get the
    _MAX_CHUNK_WORDS limit (~500 words / ~650 BPE tokens) since they can be
    arbitrarily long. Long narrative chunks waste embedding model budget and
    reduce retrieval precision.
  - The embedder has a safety-net split for the rare recipe that exceeds the
    embedding model's 8192 token limit — see ingestion/embedder.py.

State machine overview:
  6 states: NARRATIVE → RECIPE_HEADER → INGREDIENTS → METHOD → RECIPE_END
            TECHNIQUE_ASIDE (can break out of narrative at any time)

  Transitions are driven by two mechanisms:
    1. _line_state_candidates(): structural line analysis (faster, no regex cost)
       — header shape detection, ingredient start detection, method verb detection
    2. _tripwire_check(): regex patterns on every sentence (always runs)
       — catches signals that line structure alone misses

  The state machine processes one page at a time, accumulating chunk text.
  When transitioning OUT of a recipe state group (header/ingredients/method/end)
  to a non-recipe state (narrative/technique), it flushes the accumulated
  recipe chunk before starting the new chunk.

See: .gsd/milestones/M015/slices/S03/S03-CONTEXT.md for enrichment contract.
"""

import re
from enum import Enum

from app.models.enums import ChunkType

# Max words for non-recipe chunks (narrative, technique asides).
# ~500 words ≈ 650 BPE tokens — good retrieval precision, low LLM token burn.
# Recipes are exempt — kept whole for RAG retrieval quality.
_MAX_CHUNK_WORDS = 500

# Sentence boundary splitter: splits after .!? followed by whitespace.
# Used to divide long narrative lines into sentence-level events for
# state transition detection (the state machine operates at sentence granularity
# for narrative content, line granularity for recipe content).
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

# Ingredient line start: a digit or a fraction character.
# "2 cups flour" → ingredient. "½ teaspoon salt" → ingredient.
_AMOUNT_START = re.compile(r"^(?:\d|[%¼½¾⅓⅔⅛⅜⅝⅞])")

# Method step start: common cooking verbs at the beginning of a sentence.
# Numbered steps ("1. Preheat the oven") are caught by TRIPWIRES instead.
_RECIPE_VERB_START = re.compile(
    r"^(?:heat|add|stir|fold|whisk|season|bake|roast|cook|combine|mix|beat|drop|pour|brush|chop|mold|fry|put|cover|serve)\b",
    re.I,
)

# Section headings that appear in cookbook PDF ToC/index pages — not recipe titles.
# These are checked in _looks_like_recipe_header() to avoid misclassifying
# "DESSERTS" (a chapter heading) as a recipe header.
_GENERIC_NON_RECIPE_HEADINGS = {
    "THE SOUTHERN COOK BOOK",
    "INDEX",
    "APPETIZERS",
    "DESSERTS",
    "SALADS",
    "SOUPS",
    "MEATS",
    "FISH",
    "VEGETABLES",
    "CAKES",
    "PIES",
}

# Terms that appear in cookbook index headings — used for front-matter detection.
# A page where most lines are these terms + page numbers is an index page, not recipes.
_INDEX_HEADING_TERMS = frozenset(
    {
        "APPETIZERS",
        "BEVERAGES",
        "BREAD",
        "BISCUITS",
        "CAKES",
        "CANDIES",
        "EGGS",
        "FRITTERS",
        "PANCAKES",
        "WAFFLES",
        "MUSH",
        "FRUIT",
        "VEGETABLES",
        "ICINGS",
        "JELLIES",
        "JAMS",
        "MEAT",
        "POULTRY",
        "PUDDINGS",
        "SALADS",
        "RELISHES",
        "SAUCES",
        "DRESSINGS",
        "SEA",
        "FOOD",
        "SMALL",
        "COOKIES",
        "SOUPS",
    }
)

# States that can start a new recipe — transitioning TO one of these states
# from NARRATIVE begins recipe accumulation.
_RECIPE_START_STATES = frozenset(
    {
        "recipe_header",
        "recipe_end",
    }
)

# States that belong to a recipe lifecycle — these accumulate into one chunk.
# Transitioning WITHIN this set doesn't flush the chunk — the recipe is still
# in progress. Only transitioning OUT of this set (to NARRATIVE or TECHNIQUE_ASIDE)
# triggers a flush.
_RECIPE_STATES = frozenset(
    {
        "recipe_header",
        "ingredients",
        "method",
        "recipe_end",
    }
)


class CookbookState(str, Enum):
    NARRATIVE = "narrative"
    RECIPE_HEADER = "recipe_header"
    INGREDIENTS = "ingredients"
    METHOD = "method"
    TECHNIQUE_ASIDE = "technique_aside"
    RECIPE_END = "recipe_end"


# Tripwire patterns: regex signals that fire on any sentence and indicate a
# probable state transition. Called cheaply on every sentence before falling
# back to LLM classification.
#
# Why tripwires instead of always using the LLM?
#   - ~500 sentences per cookbook page × many pages = thousands of LLM calls
#   - Tripwires catch ~90-95% of transitions for free
#   - LLM is reserved for ambiguous cases where heuristics disagree
TRIPWIRES = {
    CookbookState.RECIPE_HEADER: [
        r"\b(serves?|yield[s]?|makes?)\s+\d",  # "Serves 4" in a header line
    ],
    CookbookState.INGREDIENTS: [
        r"^\d+\s*(g|kg|ml|l|tbsp?|tsp?|cup)",  # metric quantities
        r"^(for the|ingredients?:)",            # explicit section labels
    ],
    CookbookState.METHOD: [
        r"^\d+\.\s+",                           # numbered steps "1. Preheat"
        r"^(method|instructions?|directions?|preparation):",
        r"\b(heat|add|stir|fold|whisk|season|bake|roast)\b",
    ],
    CookbookState.TECHNIQUE_ASIDE: [
        r"\b(why (this|it) works|the science|the reason)\b",
        r"\b(maillard|emulsif|gelatini)\b",     # culinary science vocabulary
    ],
    CookbookState.RECIPE_END: [
        r"\b(serve immediately|to serve|serving suggestion|note:)\b",
    ],
}


def _tripwire_check(sentence: str) -> list[CookbookState]:
    """Run all tripwire patterns against a sentence. Returns matching states.

    O(patterns) per sentence — currently ~10 total patterns across all states.
    Called on every sentence during page processing, so must stay fast.
    """
    candidates = []
    for state, patterns in TRIPWIRES.items():
        if any(re.search(p, sentence, re.I | re.M) for p in patterns):
            candidates.append(state)
    return candidates


def _normalize_page_lines(page_text: str) -> list[str]:
    """Collapse OCR line noise into meaningful content lines.

    Historical cookbook scans often split ingredient quantities and words across
    many short lines due to column-based layout. We keep line structure for
    header detection, but stitch obviously wrapped ingredient/method fragments
    back together.

    Merge heuristics (should_merge conditions):
      - line <= 18 chars: very short lines are almost certainly OCR-wrapped fragments
      - prev ends with a continuation character: comma, semicolon, bracket, preposition
      - line starts lowercase: continuation of the previous sentence
      - line starts with closing punctuation: closing bracket, comma, period

    Non-merge guards:
      - prev is a short recipe header (≤3 words): don't merge the title into
        the first ingredient line
      - next_to_page_marker: don't merge a page number into a header
      - catalog lines: index entry lines should stay on their own line
    """
    raw_lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    if not raw_lines:
        return []

    def _is_page_marker(text: str) -> bool:
        return bool(re.fullmatch(r"\d{1,3}", text.strip()))

    merged: list[str] = []
    for line in raw_lines:
        if not merged:
            merged.append(line)
            continue

        prev = merged[-1]
        should_merge = False
        if not _looks_like_recipe_header(line):
            prev_is_short_header = _looks_like_recipe_header(prev) and len(prev.split()) <= 3
            next_to_page_marker = _is_page_marker(prev) and _looks_like_recipe_header(line)
            should_merge = (
                not prev_is_short_header
                and not next_to_page_marker
                and not _looks_like_catalog_line(prev)
                and not _looks_like_catalog_line(line)
                and (
                    len(line) <= 18
                    or prev.endswith((",", ";", "(", "of", "and", "with", "to", "the", "a", "an", "-"))
                    or line[:1].islower()
                    or re.match(r"^[)\],.;:]", line)
                )
            )

        if should_merge:
            merged[-1] = f"{prev} {line}".strip()
        else:
            merged.append(line)
    return merged


def _looks_like_recipe_header(line: str) -> bool:
    """Heuristically determine whether a line looks like a recipe title.

    Recipe headers in historical cookbook scans tend to be:
      - 2-5 Title-Cased or Mixed-Case words (not all-caps — those are chapter headings)
      - No colons, semicolons, or ellipses (which appear in method steps and indexes)
      - No digits within words (ingredient quantities start with digits)
      - Short (< 60 chars) — recipe names rarely exceed that length

    Negative signals (immediate rejection):
      - In _GENERIC_NON_RECIPE_HEADINGS: known non-recipe section heading
      - Pure digit: page number
      - Starts with amount character: ingredient line
      - Starts with cooking verb: method step
      - Starts with "(" or punctuation: continuation fragment
      - Contains "page", "index", "copyright": navigation content
      - Contains ":", ";", "!", "...": method or index content
      - Multiple bare numbers: index entry or table

    Length/capitalisation checks:
      - All words must start with uppercase (title case or all-caps allowed)
      - But all-caps multi-word is rejected (section heading not recipe)
      - Single short alpha word is rejected (too ambiguous)
    """
    text = line.strip()
    if not text:
        return False
    if text.upper() in _GENERIC_NON_RECIPE_HEADINGS:
        return False
    if re.fullmatch(r"\d+", text):
        return False
    if len(text) < 3 or len(text) > 60:
        return False
    if _AMOUNT_START.match(text):
        return False
    if _RECIPE_VERB_START.match(text):
        return False
    if text.startswith(("(", "%", "^", "*", '"', "'", ".", ",", "-")):
        return False
    if re.search(r"\b(page|copyright|compiled|edited|index|collection|library|california|news|recipes|cook book|continued|press|reading|box|avenue|street)\b", text, re.I):
        return False
    if any(ch in text for ch in [":", ";", "!", "...."]):
        return False
    if len(re.findall(r"\b\d{1,3}\b", text)) >= 2:
        return False
    if re.search(r"\b(?:page|pages)\b", text, re.I):
        return False
    if re.search(r"\.{2,}", text):
        return False

    words = text.split()
    if len(words) == 0 or len(words) > 5:
        return False

    alpha_words = [w for w in words if re.search(r"[A-Za-z]", w)]
    if not alpha_words:
        return False
    # Single very short alpha word: ambiguous (could be "Soup" as a section heading)
    if len(alpha_words) == 1 and len(re.sub(r"[^A-Za-z]", "", alpha_words[0])) <= 4:
        return False
    # Single alpha word: still ambiguous even if longer
    if len(alpha_words) == 1:
        return False
    # All words are 1-2 letter abbreviations: not a recipe title
    if all(len(re.sub(r"[^A-Za-z]", "", w)) <= 2 for w in alpha_words):
        return False

    capitalized_words = sum(1 for w in alpha_words if w[:1].isupper())
    uppercase_words = sum(1 for w in alpha_words if w.isupper())
    # Title case: all alpha words must start with uppercase
    if capitalized_words < len(alpha_words):
        return False
    # All-caps multi-word: section heading (e.g. "ROAST CHICKEN"), not a recipe title
    if uppercase_words == len(alpha_words) and len(alpha_words) > 1:
        return False
    # No digits within words (ingredient quantities have digits)
    if any(re.search(r"\d", w) for w in words):
        return False

    return True


def _looks_like_ingredient_line(line: str) -> bool:
    """Quick check for ingredient lines — amount start or common pantry staples.

    The pantry staple list (salt, pepper, etc.) catches ingredient lines that
    don't start with a quantity — e.g. "Salt to taste" or "Pepper" — which
    _AMOUNT_START alone would miss.
    """
    text = line.strip()
    if not text:
        return False
    if _AMOUNT_START.match(text):
        return True
    return bool(re.match(r"^(salt|pepper|paprika|parsley|flour|butter|sugar)\b", text, re.I))


def _looks_like_catalog_line(line: str) -> bool:
    """Detect index/catalog lines: "Recipe Name ... 42" or "Chicken Soup   38".

    Index entries in cookbook scans typically:
      - Have 2+ bare numbers (the page number and possibly another reference)
      - Are not ingredient lines (no leading amount)
      - Are not method lines (no leading cooking verb)
      - Have no special punctuation (colons, semicolons — those are method steps)
      - Have multiple title-cased words (the recipe name component)

    Used to prevent merging adjacent index entries in _normalize_page_lines
    and to guard against mistaking catalog pages for recipe content.
    """
    stripped = line.strip()
    if not stripped:
        return False
    digit_tokens = re.findall(r"\b\d{1,3}\b", stripped)
    if len(digit_tokens) < 2:
        return False
    if _RECIPE_VERB_START.match(stripped) or _AMOUNT_START.match(stripped):
        return False
    if any(ch in stripped for ch in (":", ";", "!", "?")):
        return False
    words = re.findall(r"[A-Za-z']+", stripped)
    if len(words) < 3:
        return False
    if sum(1 for word in words if word[:1].isupper()) < max(2, len(words) // 2):
        return False
    return True


def _line_state_candidates(line: str, next_line: str | None = None) -> list[CookbookState]:
    """Determine candidate states for a line using structural heuristics.

    Structural detection (this function) runs before tripwire detection —
    it's faster and more reliable for clear cases (header shape, ingredient
    amounts, method verbs). Tripwire results are added after.

    next_line lookahead: a line that looks like a recipe header is only
    classified as RECIPE_HEADER if the following line starts a recipe
    (ingredient or method). This prevents chapter headings from being
    treated as recipe headers when followed by narrative prose.

    same_line_recipe guard: a header-like line that itself contains an
    ingredient amount (run-on format) is also classified as RECIPE_HEADER
    even without lookahead — the ingredient content confirms it's a recipe.
    """
    candidates: list[CookbookState] = []
    stripped = line.strip()
    header_like = _looks_like_recipe_header(stripped)
    next_starts_recipe = bool(
        next_line and (_looks_like_ingredient_line(next_line) or _RECIPE_VERB_START.match(next_line.strip()))
    )
    same_line_recipe = bool(header_like and (" " in stripped) and _looks_like_ingredient_line(stripped))

    if header_like and (next_starts_recipe or same_line_recipe):
        candidates.append(CookbookState.RECIPE_HEADER)
    if _AMOUNT_START.match(stripped):
        candidates.append(CookbookState.INGREDIENTS)
    if _RECIPE_VERB_START.match(stripped):
        candidates.append(CookbookState.METHOD)
    # Add tripwire candidates, but skip RECIPE_HEADER/INGREDIENTS/METHOD — those
    # were handled above with more context (lookahead) than tripwires provide.
    candidates.extend(
        state
        for state in _tripwire_check(stripped)
        if state not in {CookbookState.RECIPE_HEADER, CookbookState.INGREDIENTS, CookbookState.METHOD}
    )
    return candidates


def _sentence_state_candidates(text: str) -> list[CookbookState]:
    """Determine candidate states for a sentence fragment within a line.

    Used for intra-line sentence splitting in narrative content — when a
    single line contains multiple sentences, each sentence is checked
    independently for state signals. Simpler than _line_state_candidates
    because lookahead isn't available at sentence granularity.
    """
    stripped = text.strip()
    if not stripped:
        return []
    candidates: list[CookbookState] = []
    if _AMOUNT_START.match(stripped):
        candidates.append(CookbookState.INGREDIENTS)
    if _RECIPE_VERB_START.match(stripped):
        candidates.append(CookbookState.METHOD)
    candidates.extend(
        state
        for state in _tripwire_check(stripped)
        if state not in {CookbookState.RECIPE_HEADER, CookbookState.INGREDIENTS, CookbookState.METHOD}
    )
    return candidates


def _looks_like_index_entry(line: str) -> bool:
    """Detect lines of the form "Recipe Name   42" (title + single page number).

    Used in _is_front_matter_or_index_page() to identify index pages.
    Strict detection to avoid false-positives on ingredient lines:
      - Exactly one bare number, and it must be at the end
      - Contains alphabetic text
      - Not an ingredient or method line
      - Has 2+ title-cased words
    """
    stripped = line.strip()
    if not stripped:
        return False
    if len(re.findall(r"\b\d{1,3}\b", stripped)) != 1:
        return False
    if not re.search(r"[A-Za-z]", stripped):
        return False
    if not re.search(r"\b\d{1,3}\b\s*$", stripped):
        return False
    if _RECIPE_VERB_START.match(stripped) or _AMOUNT_START.match(stripped):
        return False
    if any(ch in stripped for ch in (":", ";", "!", "?")):
        return False
    words = re.findall(r"[A-Za-z']+", stripped)
    if len(words) < 2:
        return False
    return sum(1 for word in words if word[:1].isupper()) >= max(2, len(words) - 1)


def _is_front_matter_or_index_page(lines: list[str]) -> bool:
    """Determine if a page is front matter or an index rather than recipe content.

    Called at the start of each page to set page_blocks_recipe_entry, which
    prevents the state machine from entering recipe states on non-recipe pages.

    Signal counting approach (not a single rule):
      - heading_lines: lines matching known section heading terms (INDEX, DESSERTS, etc.)
      - index_entries: lines matching the "Name   42" index entry pattern
      - intro_like_lines: lines mentioning editorial/copyright/front-matter vocabulary
      - page_marker_lines: lines containing "page" or "(continued)"
      - embedded_index_numbers: bare numbers in non-ingredient/non-method lines
      - catalog_cluster_lines: lines with multiple bare numbers (page tables)

    Threshold combinations encode domain knowledge about what index/front-matter
    pages look like vs recipe pages. No single signal is sufficient — a real
    recipe can have a page reference; an index page has many.
    """
    if not lines:
        return False

    heading_lines = 0
    index_entries = 0
    intro_like_lines = 0
    page_marker_lines = 0
    embedded_index_numbers = 0
    catalog_cluster_lines = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper in _GENERIC_NON_RECIPE_HEADINGS or upper in _INDEX_HEADING_TERMS:
            heading_lines += 1
            continue
        if re.search(r"\bINDEX\b", stripped, re.I):
            heading_lines += 1
        if re.search(r"\b(?:page|continued)\b", stripped, re.I):
            page_marker_lines += 1
        if re.fullmatch(r"(?:page|\(continued\)|continued)", stripped, re.I):
            continue
        if _looks_like_index_entry(stripped):
            index_entries += 1
            continue
        digit_tokens = re.findall(r"\b\d{1,3}\b", stripped)
        if len(digit_tokens) >= 2 and not _RECIPE_VERB_START.match(stripped) and not _AMOUNT_START.match(stripped):
            embedded_index_numbers += len(digit_tokens)
            catalog_cluster_lines += 1
        if re.match(r"^(?:introduction|foreword|preface|contents)\b", stripped, re.I):
            intro_like_lines += 1
            continue
        if re.search(r"\b(editors?|compiled|copyright|collection|library|pamphlet|problem to select)\b", stripped, re.I):
            intro_like_lines += 1

    # Threshold combinations — each represents a different front-matter pattern:
    if heading_lines >= 2 and index_entries >= 3:
        return True  # Classic index page: chapter heading + multiple entries
    if heading_lines >= 1 and embedded_index_numbers >= 6:
        return True  # Table of contents with page numbers
    if page_marker_lines >= 1 and catalog_cluster_lines >= 3:
        return True  # Page-reference table
    if index_entries >= max(5, len(lines) // 3):
        return True  # Majority of lines are index entries
    if intro_like_lines >= 2 and index_entries == 0:
        return True  # Pure front matter prose
    if intro_like_lines >= 1 and heading_lines >= 1 and page_marker_lines >= 1:
        return True  # Mixed front matter indicators
    return False


def _allows_recipe_entry_from_narrative(
    candidates: list[CookbookState],
    event_text: str,
    lines: list[str],
) -> bool:
    """Decide whether the state machine should enter recipe mode from NARRATIVE.

    Entry from NARRATIVE to recipe states is the most consequential transition —
    a false positive (entering recipe mode on a non-recipe line) pollutes the
    chunk with wrong content.

    Guards:
      1. _is_front_matter_or_index_page(): block all recipe entry on index/front-matter pages
      2. candidates must include a recipe start state (header or end)
         OR the event text itself looks like a recipe header
      3. catalog-like lines are blocked even if they trigger a signal

    Why allow RECIPE_END as a start state?
      "To serve" sentences sometimes appear in narrative prose before a recipe
      is explicitly named. Allowing RECIPE_END as an entry allows the state
      machine to capture these serving notes as part of the upcoming recipe chunk.
    """
    if not candidates:
        return False
    if _is_front_matter_or_index_page(lines):
        return False
    if any(candidate.value in _RECIPE_START_STATES for candidate in candidates):
        return True
    stripped = event_text.strip()
    if _looks_like_recipe_header(stripped):
        return True
    # Catalog/index lines may trigger tripwires (e.g. "serves" in "Serves 4 ... page 82")
    # but are not real recipe entry points.
    return not _looks_like_catalog_line(stripped)


def _is_recipe_state(state: CookbookState) -> bool:
    """True if state is part of the recipe accumulation group."""
    return state.value in _RECIPE_STATES


def _looks_like_non_recipe_heading(line: str) -> bool:
    """True if the line is a known section heading that breaks recipe continuity."""
    stripped = line.strip()
    if not stripped:
        return False
    upper = stripped.upper()
    if upper in _GENERIC_NON_RECIPE_HEADINGS:
        return True
    return bool(re.match(r"^(?:INDEX|INTRODUCTION|FOREWORD|PREFACE|CONTENTS)\b", stripped, re.I))


def _looks_like_recipe_continuation(line: str) -> bool:
    """True if a line from the next page looks like it continues the current recipe.

    Used at page boundaries: when mid-recipe and the next page starts, we need
    to decide whether the recipe spans the page break or the page break ends it.

    If the first line of the new page is an ingredient or method continuation,
    keep accumulating. If it's a new recipe heading, narrative, or non-recipe
    heading, flush the current recipe chunk before processing the new page.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if _looks_like_ingredient_line(stripped) or _RECIPE_VERB_START.match(stripped):
        return True
    lowered = stripped.lower()
    continuation_starts = (
        "then ",
        "when ",
        "cook ",
        "add ",
        "stir ",
        "mix ",
        "pour ",
        "place ",
        "serve ",
        "remove ",
        "reduce ",
    )
    return lowered.startswith(continuation_starts)


def _event_parts(line: str) -> list[str]:
    """Split a line into processable events (sentences or single structured lines).

    Recipe content lines (headers, ingredient lines) are returned as a single
    event — don't split them into sentences, the structure is load-bearing.

    Narrative lines may contain multiple sentences — split them so the state
    machine can detect transitions mid-line (e.g. a narrative sentence followed
    by a "To serve..." sentence that transitions to RECIPE_END).

    Returns at least one event even for lines that don't split cleanly.
    """
    stripped = line.strip()
    if not stripped:
        return []
    # Structured recipe lines: treat as single events (don't sentence-split)
    if _looks_like_recipe_header(stripped) or _looks_like_ingredient_line(stripped):
        return [stripped]

    parts = [part.strip() for part in _SENTENCE_BOUNDARY.split(stripped) if part.strip()]
    return parts or [stripped]


def run_state_machine(pages: list[dict]) -> list[dict]:
    """
    Process pages through cookbook state machine.
    Returns list of chunks: {text, chunk_type, chapter, page_number}

    Recipe states (header, ingredients, method, end) accumulate into a
    single chunk so the full recipe is retrievable as one unit from Pinecone.
    Only a transition OUT of recipe context (to narrative, technique aside,
    or a new recipe header) flushes the recipe chunk.

    flush_chunk() closure:
      Defined inside run_state_machine to capture current_chunk, current_chapter,
      and chunks by reference. Handles the word-limit splitting for non-recipe
      chunks — recipe chunks are always written whole regardless of length.

    Page boundary handling:
      At each new page, if mid-recipe, we check the first line of the new page.
      If it's a non-recipe heading, a new recipe header, or doesn't look like
      a continuation, we flush the current recipe chunk and reset to NARRATIVE.
      This prevents a recipe from inadvertently absorbing front matter from a
      following page when the pages are processed sequentially.

    event_index guard:
      When processing sentence fragments (event_index > 0) within a line,
      METHOD transitions from NARRATIVE are suppressed. This prevents the state
      machine from interpreting a cooking verb mid-sentence in narrative prose
      as the start of a recipe method section.
    """
    if not pages:
        return []

    current_state = CookbookState.NARRATIVE
    current_chunk: list[str] = []
    chunks = []
    current_chapter = ""
    current_page_num = pages[0]["page_number"]

    def flush_chunk(state, page_num, chunk_type=None):
        nonlocal current_chunk
        if current_chunk:
            text = " ".join(current_chunk).strip()
            if len(text) > 20:  # Ignore trivially short fragments
                # Determine chunk type from state unless overridden
                ctype = chunk_type or (
                    ChunkType.RECIPE
                    if _is_recipe_state(state)
                    else ChunkType.TECHNIQUE
                    if state == CookbookState.TECHNIQUE_ASIDE
                    else ChunkType.INTRO
                )
                base = {
                    "chunk_type": ctype.value,
                    "chapter": current_chapter,
                    "page_number": page_num,
                }
                # Recipe chunks: always write whole (no word limit)
                # Non-recipe chunks: split at sentence boundaries if over word limit
                if _is_recipe_state(state) or len(text.split()) <= _MAX_CHUNK_WORDS:
                    chunks.append({**base, "text": text})
                else:
                    # Split long narrative/technique chunks at sentence boundaries
                    sentences = _SENTENCE_SPLIT.split(text)
                    buf: list[str] = []
                    buf_words = 0
                    for sent in sentences:
                        sent_words = len(sent.split())
                        if buf and buf_words + sent_words > _MAX_CHUNK_WORDS:
                            chunks.append({**base, "text": " ".join(buf)})
                            buf = []
                            buf_words = 0
                        buf.append(sent)
                        buf_words += sent_words
                    if buf:
                        joined = " ".join(buf)
                        if len(joined) > 20:
                            chunks.append({**base, "text": joined})
            current_chunk = []

    for page in pages:
        page_num = page["page_number"]
        page_text = page["text"].strip()
        if not page_text:
            continue

        lines = _normalize_page_lines(page_text)
        # Pre-check this page for front matter / index — used to block recipe entry
        page_blocks_recipe_entry = _is_front_matter_or_index_page(lines)

        # Page boundary: if mid-recipe and this is a new page, decide whether
        # to continue accumulating or flush and reset.
        if current_chunk and _is_recipe_state(current_state) and page_num != current_page_num:
            first_line = next((line for line in lines if line.strip()), "")
            if (
                _looks_like_non_recipe_heading(first_line)
                or _looks_like_recipe_header(first_line)
                or not _looks_like_recipe_continuation(first_line)
            ):
                flush_chunk(current_state, current_page_num)
                current_state = CookbookState.NARRATIVE

        for index, line in enumerate(lines):
            next_line = lines[index + 1] if index + 1 < len(lines) else None
            line_candidates = _line_state_candidates(line, next_line)
            events = _event_parts(line)

            for event_index, event_text in enumerate(events):
                # First event in a line: use line-level candidates (have lookahead context)
                # Subsequent events (sentence fragments): derive candidates from sentence text
                if event_index == 0:
                    candidates = line_candidates
                    # Lookahead-based RECIPE_HEADER detection for first events
                    if not candidates and _looks_like_recipe_header(event_text):
                        lookahead = next_line.strip() if next_line else ""
                        if lookahead and (_looks_like_ingredient_line(lookahead) or _RECIPE_VERB_START.match(lookahead)):
                            candidates = [CookbookState.RECIPE_HEADER]
                else:
                    candidates = _sentence_state_candidates(event_text)

                # Suppress METHOD transitions on mid-line sentence fragments when
                # currently in NARRATIVE — cooking verbs mid-sentence are likely prose,
                # not recipe method steps.
                if event_index > 0 and current_state == CookbookState.NARRATIVE:
                    candidates = [c for c in candidates if c != CookbookState.METHOD]

                # From NARRATIVE: apply guards before allowing recipe entry
                if current_state == CookbookState.NARRATIVE and candidates:
                    if not _allows_recipe_entry_from_narrative(candidates, event_text, lines):
                        candidates = []
                    elif page_blocks_recipe_entry and all(
                        candidate != CookbookState.RECIPE_HEADER for candidate in candidates
                    ):
                        # On index/front-matter pages, only explicit RECIPE_HEADER signals
                        # can override the page block — TECHNIQUE and RECIPE_END cannot.
                        candidates = []

                # From non-recipe state: only allow transitions to recipe-adjacent states.
                # Can't go directly from NARRATIVE to RECIPE_END without a header first.
                if not _is_recipe_state(current_state):
                    candidates = [
                        c
                        for c in candidates
                        if c in {CookbookState.RECIPE_HEADER, CookbookState.INGREDIENTS, CookbookState.METHOD, CookbookState.TECHNIQUE_ASIDE}
                    ]
                new_state = candidates[0] if candidates else current_state

                # RECIPE_HEADER transition: always starts a new chunk.
                # Flush any in-progress recipe or non-narrative chunk first.
                if candidates and new_state == CookbookState.RECIPE_HEADER:
                    if _is_recipe_state(current_state) and current_chunk:
                        flush_chunk(current_state, current_page_num)
                    elif current_state != CookbookState.NARRATIVE and current_chunk:
                        flush_chunk(current_state, current_page_num)
                    current_chunk = [event_text]
                    current_state = CookbookState.RECIPE_HEADER
                    current_page_num = page_num
                    continue

                if candidates:
                    if new_state == current_state:
                        # Same state: accumulate into current chunk
                        current_chunk.append(event_text)
                    elif _is_recipe_state(current_state) and _is_recipe_state(new_state):
                        # Within-recipe transition (e.g. INGREDIENTS → METHOD):
                        # keep accumulating, just update the state label.
                        current_chunk.append(event_text)
                        current_state = new_state
                    else:
                        # Cross-group transition: flush current chunk, start new one
                        flush_chunk(current_state, current_page_num)
                        current_chunk = [event_text]
                        current_state = new_state
                        current_page_num = page_num
                else:
                    # No state change — accumulate into current chunk.
                    # If current_chunk is empty, record the page number for this chunk's start.
                    if not current_chunk:
                        current_page_num = page_num
                    if _is_recipe_state(current_state):
                        # Mid-recipe: keep accumulating even without a transition signal
                        current_chunk.append(event_text)
                    else:
                        if current_chunk:
                            current_chunk.append(event_text)
                        else:
                            current_chunk = [event_text]
                            current_state = CookbookState.NARRATIVE

    # End of all pages: flush any remaining chunk
    flush_chunk(current_state, current_page_num)
    return chunks
