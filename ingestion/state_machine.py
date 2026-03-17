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

from models.enums import ChunkType

# Max words for non-recipe chunks (narrative, technique asides).
# ~500 words ≈ 650 BPE tokens — good retrieval precision, low LLM token burn.
# Recipes are exempt — kept whole for RAG retrieval quality.
_MAX_CHUNK_WORDS = 500
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# States that belong to a recipe lifecycle — these accumulate into one chunk
_RECIPE_STATES = frozenset({
    "recipe_header",
    "ingredients",
    "method",
    "recipe_end",
})


class CookbookState(str, Enum):
    NARRATIVE = "narrative"
    RECIPE_HEADER = "recipe_header"
    INGREDIENTS = "ingredients"
    METHOD = "method"
    TECHNIQUE_ASIDE = "technique_aside"
    RECIPE_END = "recipe_end"


TRIPWIRES = {
    CookbookState.RECIPE_HEADER: [
        r"^[A-Z][^\n]{2,50}$",
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


def _is_recipe_state(state: CookbookState) -> bool:
    return state.value in _RECIPE_STATES


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

    def flush_chunk(state, page_num, chunk_type=None):
        if current_chunk:
            text = " ".join(current_chunk).strip()
            if len(text) > 20:
                ctype = chunk_type or (
                    ChunkType.RECIPE if _is_recipe_state(state)
                    else ChunkType.TECHNIQUE if state == CookbookState.TECHNIQUE_ASIDE
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

    for page in pages:
        page_num = page["page_number"]
        page_text = page["text"].strip()
        if not page_text:
            continue
        sentences = re.split(r"(?<=[.!?])\s+", page_text)

        for sentence in sentences:
            candidates = _tripwire_check(sentence)
            if candidates:
                new_state = candidates[0]
                if new_state == current_state:
                    # Same state — keep accumulating
                    current_chunk.append(sentence)
                elif _is_recipe_state(current_state) and _is_recipe_state(new_state):
                    # Transition within recipe lifecycle (e.g. ingredients → method)
                    # — keep accumulating into the same chunk
                    if new_state == CookbookState.RECIPE_HEADER:
                        # New recipe starting — flush the previous recipe
                        flush_chunk(current_state, page_num)
                        current_chunk = [sentence]
                    else:
                        current_chunk.append(sentence)
                    current_state = new_state
                else:
                    # Transition between non-recipe ↔ recipe or non-recipe ↔ non-recipe
                    flush_chunk(current_state, page_num)
                    current_chunk = [sentence]
                    current_state = new_state
            else:
                current_chunk.append(sentence)

    flush_chunk(current_state, pages[-1]["page_number"] if pages else 0)
    return chunks
