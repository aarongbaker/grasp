"""
ingestion/classifier.py
Phase 2b: Two-tier document classification.
  Tier 1: regex heuristics on first 3 pages (always runs, free)
  Tier 2: LLM classification only when heuristic confidence < threshold
Chef can always override via UI.
"""

import re

from app.models.enums import DocumentType

COOKBOOK_SIGNALS = [
    r"\b(serves?|yield[s]?|makes?)\s+\d",
    r"\b(tablespoon|teaspoon|tbsp|tsp|cup[s]?)\b",
    r"\b(preheat|roast|sauté|braise|fold in)\b",
    r"°[CF]\b",
    # Signals that appear in cookbook front matter / TOC / introductions
    r"\b(recipe[s]?|cookbook|cooking|cuisine|kitchen)\b",
    r"\b(appetizer|entrée|dessert|sauce|soup|salad|pastry)\b",
    r"\b(butter|flour|sugar|salt|pepper|garlic|onion)\b",
]
CULINARY_REF_SIGNALS = [
    r"\b(maillard|emulsif|gelatinis|denaturation|osmosis)\b",
    r"\b(ratio[s]?|proportion[s]?|percentage[s]?)\s+of\b",
    r"\b(food science|culinary science|modernist)\b",
]
GENERAL_KNOWLEDGE_SIGNALS = [
    r"\b(creativity|philosophy|memoir|biography)\b",
    # Removed: "chapter \d+" and "part [ivx]+" — these are structural markers
    # found in any book, not semantic signals for general knowledge content.
    # Removed: first-person patterns — common in narrative cookbooks too.
]

CONFIDENCE_THRESHOLD = 0.65


def _heuristic_classify(text: str) -> tuple[DocumentType, float]:
    cookbook_hits = sum(1 for p in COOKBOOK_SIGNALS if re.search(p, text, re.I))
    culinary_hits = sum(1 for p in CULINARY_REF_SIGNALS if re.search(p, text, re.I))
    general_hits = sum(1 for p in GENERAL_KNOWLEDGE_SIGNALS if re.search(p, text, re.I))

    total = cookbook_hits + culinary_hits + general_hits
    if total == 0:
        return DocumentType.GENERAL_KNOWLEDGE, 0.3

    if cookbook_hits >= culinary_hits and cookbook_hits >= general_hits:
        confidence = cookbook_hits / total
        return DocumentType.COOKBOOK, min(confidence, 0.95)
    elif culinary_hits >= general_hits:
        confidence = culinary_hits / total
        return DocumentType.CULINARY_REFERENCE, min(confidence, 0.90)
    else:
        confidence = general_hits / total
        return DocumentType.GENERAL_KNOWLEDGE, min(confidence, 0.85)


async def classify_document(first_pages_text: str, llm_client=None) -> DocumentType:
    """
    Classify document type. Tier 1 heuristics always run.
    Tier 2 LLM only if confidence below threshold.
    """
    doc_type, confidence = _heuristic_classify(first_pages_text)

    if confidence >= CONFIDENCE_THRESHOLD or llm_client is None:
        return doc_type

    # Tier 2: narrow LLM prompt on first 3 pages
    prompt = (
        f"Classify this document as exactly one of: cookbook, culinary_reference, general_knowledge.\n"
        f"Reply with only the classification word.\n\n"
        f"Document excerpt:\n{first_pages_text[:2000]}"
    )
    try:
        response = await llm_client.ainvoke(prompt)
        label = response.content.strip().lower()
        type_map = {t.value: t for t in DocumentType}
        return type_map.get(label, doc_type)
    except Exception:
        return doc_type
