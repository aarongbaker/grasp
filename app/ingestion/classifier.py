"""
ingestion/classifier.py

INTERNAL INFRASTRUCTURE ONLY — Phase 2b: Two-tier document classification.

After M015 pivot (cookbook de-scope), this module is used only for team/admin
curated cookbook uploads, not user-facing upload flows.

Why classify at all?
  The ingestion pipeline processes PDFs that may not be cookbooks — admins could
  accidentally upload a general reference book or culinary science text. The
  classifier gates downstream processing: only COOKBOOK and CULINARY_REFERENCE
  documents get recipe-aware chunking via the state machine. GENERAL_KNOWLEDGE
  documents get simpler narrative chunking.

Two-tier design:
  Tier 1: regex heuristics on first 3 pages (always runs, negligible cost)
  Tier 2: LLM classification only when heuristic confidence < CONFIDENCE_THRESHOLD
    ~5-10% of documents need the LLM — those with ambiguous first-page content
    (e.g. a cookbook with a long narrative preface before any recipe signals).

Why heuristics first, not LLM first?
  - Heuristics are free (no API call) and fast (~1ms vs ~500ms for LLM)
  - Most cookbooks have obvious signals on the first 3 pages (serves, tablespoon,
    ingredient words) that heuristics catch with >0.65 confidence
  - LLM classification adds latency and cost for no benefit when heuristics are confident

Confidence ceiling:
  max(confidence, 0.95) for COOKBOOK — even a 7/7 signal match is capped at 0.95
  because the heuristics don't account for adversarial inputs or edge cases.
  The ceiling prevents overconfidence from blocking the LLM fallback on genuinely
  ambiguous documents that happen to have strong surface signals.

See: .gsd/milestones/M015/slices/S03/S03-CONTEXT.md for enrichment contract.
"""

import re

from app.models.enums import DocumentType

# Signal patterns for each document type.
# More signals matched → higher confidence in that type.
# COOKBOOK_SIGNALS are intentionally broad — they appear in front matter,
# tables of contents, and introductions, not just recipe bodies.
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

# Science-forward culinary texts (modernist, food science) vs recipe collections.
# These signals distinguish a culinary reference from a general cookbook.
CULINARY_REF_SIGNALS = [
    r"\b(maillard|emulsif|gelatinis|denaturation|osmosis)\b",
    r"\b(ratio[s]?|proportion[s]?|percentage[s]?)\s+of\b",
    r"\b(food science|culinary science|modernist)\b",
]

# General knowledge / non-culinary content.
# Intentionally sparse — we'd rather classify ambiguously as COOKBOOK than
# incorrectly reject a cookbook with narrative prose.
# Note: "chapter N" and "part I" patterns were removed — these are structural
# markers found in any book, not semantic signals for GENERAL_KNOWLEDGE.
GENERAL_KNOWLEDGE_SIGNALS = [
    r"\b(creativity|philosophy|memoir|biography)\b",
]

# Minimum confidence for heuristic classification to be accepted without LLM.
# Below this threshold, the LLM is invoked if an llm_client was provided.
# 0.65 was calibrated on a test set of 20 books — adjust if classification
# accuracy degrades on new corpus additions.
CONFIDENCE_THRESHOLD = 0.65


def _heuristic_classify(text: str) -> tuple[DocumentType, float]:
    """Count signal matches and derive document type + confidence.

    Confidence model: fraction of total signal hits attributed to the
    winning type. E.g., 5 cookbook hits + 2 culinary hits + 0 general hits
    → confidence = 5/7 ≈ 0.71 for COOKBOOK.

    Edge case: zero total hits → GENERAL_KNOWLEDGE at 0.3 confidence.
    This forces the LLM tier to run if available, since no signals at all
    means the first 3 pages are unusually terse or the content is genuinely
    non-culinary.

    Confidence is capped per type to prevent overconfidence — see module docstring.
    """
    cookbook_hits = sum(1 for p in COOKBOOK_SIGNALS if re.search(p, text, re.I))
    culinary_hits = sum(1 for p in CULINARY_REF_SIGNALS if re.search(p, text, re.I))
    general_hits = sum(1 for p in GENERAL_KNOWLEDGE_SIGNALS if re.search(p, text, re.I))

    total = cookbook_hits + culinary_hits + general_hits
    if total == 0:
        # No signals at all — default to GENERAL_KNOWLEDGE at low confidence
        # so the LLM tier is invoked to make the call.
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
    Tier 2 LLM only if confidence below threshold AND llm_client provided.

    first_pages_text: concatenated text from the first 3 pages (from rasteriser).
    llm_client: optional LangChain-compatible LLM client with .ainvoke().
      If None, heuristic classification is always final regardless of confidence.
      Passing None is correct for unit tests and for contexts where LLM cost
      is not justified (e.g. re-classifying known cookbooks on re-ingestion).

    LLM prompt design:
      - Extremely narrow: "classify as exactly one of: cookbook, culinary_reference,
        general_knowledge" — no chain-of-thought, no explanation
      - Input truncated to first 2000 chars — sufficient signal, minimises token cost
      - Falls back to heuristic result on LLM error (network failure, API error)

    Return value: the heuristic type if confidence >= threshold, otherwise the
    LLM-determined type (or heuristic fallback if LLM fails).
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
        # Map the LLM's label back to a DocumentType enum value.
        # If the LLM returns an unexpected string, fall back to heuristic result.
        type_map = {t.value: t for t in DocumentType}
        return type_map.get(label, doc_type)
    except Exception:
        # LLM failure: fall back to heuristic result rather than crashing.
        # The ingestion pipeline continues with the heuristic classification —
        # a misclassified document is better than a failed ingestion.
        return doc_type
