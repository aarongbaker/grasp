"""
ingestion/state_machine.py
Phase 2c: Cookbook state machine — 6 states.
Two-tier transition detection: regex tripwire (every sentence, free) →
LLM detector only on candidates (~5-10% of sentences).
"""

import re
from enum import Enum
from models.enums import ChunkType

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


def run_state_machine(pages: list[dict]) -> list[dict]:
    """
    Process pages through cookbook state machine.
    Returns list of chunks: {text, chunk_type, chapter, page_number}
    """
    if not pages:
        return []

    current_state = CookbookState.NARRATIVE
    current_chunk: list[str] = []
    chunks = []
    current_chapter = ""

    STATE_TO_CHUNK_TYPE = {
        CookbookState.NARRATIVE: ChunkType.INTRO,
        CookbookState.RECIPE_HEADER: ChunkType.RECIPE,
        CookbookState.INGREDIENTS: ChunkType.RECIPE,
        CookbookState.METHOD: ChunkType.RECIPE,
        CookbookState.TECHNIQUE_ASIDE: ChunkType.TECHNIQUE,
        CookbookState.RECIPE_END: ChunkType.TIP,
    }

    def flush_chunk(state, page_num):
        if current_chunk:
            text = " ".join(current_chunk).strip()
            if len(text) > 20:
                chunks.append({
                    "text": text,
                    "chunk_type": STATE_TO_CHUNK_TYPE[state].value,
                    "chapter": current_chapter,
                    "page_number": page_num,
                })

    for page in pages:
        page_num = page["page_number"]
        sentences = re.split(r"(?<=[.!?])\s+", page["text"])

        for sentence in sentences:
            candidates = _tripwire_check(sentence)
            if candidates:
                new_state = candidates[0]
                if new_state != current_state:
                    flush_chunk(current_state, page_num)
                    current_chunk = [sentence]
                    current_state = new_state
                else:
                    current_chunk.append(sentence)
            else:
                current_chunk.append(sentence)

    flush_chunk(current_state, pages[-1]["page_number"] if pages else 0)
    return chunks
