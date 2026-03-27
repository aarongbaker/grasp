"""
ingestion/state_machine.py
Phase 2c: Cookbook state machine — 6 states.
Two-tier transition detection: regex tripwire (every sentence, free) →
LLM detector only on candidates (~5-10% of sentences).

Chunking philosophy:
  - A complete recipe (header → ingredients → method → end) is ONE chunk.
    Splitting a recipe across chunks destroys RAG retrieval quality.
  - Narrative, technique asides, and other non-recipe content get the
    _MAX_CHUNK_WORDS limit since they can be arbitrarily long.
  - Recipe chunks are exempt from the word limit — a 3000-word recipe
    is more useful as one chunk than two halves. The embedder has a
    safety-net split for the rare recipe that exceeds the embedding
    model's token limit.
"""

import re
from enum import Enum

from app.models.enums import ChunkType

# Max words for non-recipe chunks (narrative, technique asides).
# ~500 words ≈ 650 BPE tokens — good retrieval precision, low LLM token burn.
# Recipes are exempt — kept whole for RAG retrieval quality.
_MAX_CHUNK_WORDS = 500
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_AMOUNT_START = re.compile(r"^(?:\d|[%¼½¾⅓⅔⅛⅜⅝⅞]|[IVXivx]+[½¼¾⅓⅔⅛⅜⅝⅞]?)")
_RECIPE_VERB_START = re.compile(
    r"^(?:heat|add|stir|fold|whisk|season|bake|roast|cook|combine|mix|beat|drop|pour|brush|chop|mold|fry|put|cover|serve)\b",
    re.I,
)
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

# States that belong to a recipe lifecycle — these accumulate into one chunk
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


TRIPWIRES = {
    CookbookState.RECIPE_HEADER: [
        r"\b(serves?|yield[s]?|makes?)\s+\d",
    ],
    CookbookState.INGREDIENTS: [
        r"^\d+\s*(g|kg|ml|l|tbsp?|tsp?|cup)",
        r"^(for the|ingredients?:)",
    ],
    CookbookState.METHOD: [
        r"^\d+\.\s+",
        r"^(method|instructions?|directions?|preparation):",
        r"\b(heat|add|stir|fold|whisk|season|bake|roast)\b",
    ],
    CookbookState.TECHNIQUE_ASIDE: [
        r"\b(why (this|it) works|the science|the reason)\b",
        r"\b(maillard|emulsif|gelatini)\b",
    ],
    CookbookState.RECIPE_END: [
        r"\b(serve immediately|to serve|serving suggestion|note:)\b",
    ],
}


def _tripwire_check(sentence: str) -> list[CookbookState]:
    candidates = []
    for state, patterns in TRIPWIRES.items():
        if any(re.search(p, sentence, re.I | re.M) for p in patterns):
            candidates.append(state)
    return candidates


def _normalize_page_lines(page_text: str) -> list[str]:
    """Collapse OCR line noise into meaningful content lines.

    Historical cookbook scans often split ingredient quantities and words across
    many short lines. We keep line structure for header detection, but stitch
    obviously wrapped ingredient/method fragments back together.
    """
    raw_lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    if not raw_lines:
        return []

    merged: list[str] = []
    for line in raw_lines:
        if not merged:
            merged.append(line)
            continue

        prev = merged[-1]
        should_merge = False
        if not _looks_like_recipe_header(line):
            prev_is_short_header = _looks_like_recipe_header(prev) and len(prev.split()) <= 3
            should_merge = (
                not prev_is_short_header
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

    words = text.split()
    if len(words) == 0 or len(words) > 5:
        return False

    alpha_words = [w for w in words if re.search(r"[A-Za-z]", w)]
    if not alpha_words:
        return False
    if len(alpha_words) == 1 and len(re.sub(r"[^A-Za-z]", "", alpha_words[0])) <= 4:
        return False
    if all(len(re.sub(r"[^A-Za-z]", "", w)) <= 2 for w in alpha_words):
        return False

    capitalized_words = sum(1 for w in alpha_words if w[:1].isupper())
    uppercase_words = sum(1 for w in alpha_words if w.isupper())
    if capitalized_words < len(alpha_words):
        return False
    if uppercase_words == len(alpha_words) and len(alpha_words) > 1:
        return False
    if any(re.search(r"\d", w) for w in words):
        return False

    return True


def _looks_like_ingredient_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if _AMOUNT_START.match(text):
        return True
    return bool(re.match(r"^(salt|pepper|paprika|parsley|flour|butter|sugar)\b", text, re.I))


def _line_state_candidates(line: str, next_line: str | None = None) -> list[CookbookState]:
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
    candidates.extend(
        state
        for state in _tripwire_check(stripped)
        if state not in {CookbookState.RECIPE_HEADER, CookbookState.INGREDIENTS, CookbookState.METHOD}
    )
    return candidates


def _sentence_state_candidates(text: str) -> list[CookbookState]:
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


def _is_recipe_state(state: CookbookState) -> bool:
    return state.value in _RECIPE_STATES


def _event_parts(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped:
        return []
    if _looks_like_recipe_header(stripped) or _looks_like_ingredient_line(stripped):
        return [stripped]

    parts = [part.strip() for part in _SENTENCE_BOUNDARY.split(stripped) if part.strip()]
    return parts or [stripped]


def run_state_machine(pages: list[dict]) -> list[dict]:
    """
    Process pages through cookbook state machine.
    Returns list of chunks: {text, chunk_type, chapter, page_number}

    Recipe states (header, ingredients, method, end) accumulate into a
    single chunk so the full recipe is retrievable as one unit.
    Only a transition OUT of recipe context (to narrative, technique aside,
    or a new recipe header) flushes the recipe chunk.
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
            if len(text) > 20:
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
                # Recipe chunks stay whole; non-recipe chunks get word limit
                if _is_recipe_state(state) or len(text.split()) <= _MAX_CHUNK_WORDS:
                    chunks.append({**base, "text": text})
                else:
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
        for index, line in enumerate(lines):
            next_line = lines[index + 1] if index + 1 < len(lines) else None
            line_candidates = _line_state_candidates(line, next_line)
            events = _event_parts(line)

            for event_index, event_text in enumerate(events):
                if event_index == 0:
                    candidates = line_candidates
                    if not candidates and _looks_like_recipe_header(event_text):
                        lookahead = next_line.strip() if next_line else ""
                        if lookahead and (_looks_like_ingredient_line(lookahead) or _RECIPE_VERB_START.match(lookahead)):
                            candidates = [CookbookState.RECIPE_HEADER]
                else:
                    candidates = _sentence_state_candidates(event_text)

                if event_index > 0 and current_state == CookbookState.NARRATIVE:
                    candidates = [c for c in candidates if c != CookbookState.METHOD]

                if not _is_recipe_state(current_state):
                    candidates = [
                        c
                        for c in candidates
                        if c in {CookbookState.RECIPE_HEADER, CookbookState.INGREDIENTS, CookbookState.METHOD, CookbookState.TECHNIQUE_ASIDE}
                    ]
                new_state = candidates[0] if candidates else current_state

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
                        current_chunk.append(event_text)
                    elif _is_recipe_state(current_state) and _is_recipe_state(new_state):
                        current_chunk.append(event_text)
                        current_state = new_state
                    else:
                        flush_chunk(current_state, current_page_num)
                        current_chunk = [event_text]
                        current_state = new_state
                        current_page_num = page_num
                else:
                    if not current_chunk:
                        current_page_num = page_num
                    if _is_recipe_state(current_state):
                        current_chunk.append(event_text)
                    else:
                        if current_chunk:
                            current_chunk.append(event_text)
                        else:
                            current_chunk = [event_text]
                            current_state = CookbookState.NARRATIVE

    flush_chunk(current_state, current_page_num)
    return chunks
