"""api/routes/ingest.py — Internal/admin-only PDF upload and ingestion job polling.

INTERNAL INFRASTRUCTURE ONLY — These routes are not exposed in the active product UI.
After the M015 pivot (cookbook de-scope), GRASP focuses on menu-intent session creation
and uses curated cookbook text only as internal enrichment input.

These routes remain in the codebase to support:
- Team/admin curated cookbook uploads for RAG enrichment
- Future admin UI for library management
- Internal testing and development workflows

User-facing cookbook upload, browsing, and selection were removed in M015/S02.
Session creation uses only free-text menu intent (`POST /api/v1/sessions`).

See: .gsd/milestones/M015/slices/S03/S03-CONTEXT.md for the full enrichment contract.

--- Recipe title extraction (the bulk of this module) ---

When the state machine classifies a chunk as RECIPE, we need a human-readable
title for that chunk. Cookbook PDFs don't mark titles consistently — some use
ALL CAPS headings, some have the title run directly into the ingredient list on
the same line ("Roast Chicken 1 whole chicken 3 tbsp olive oil..."), some put
the recipe name in a column separate from the body.

The extraction pipeline has two tiers:
  1. _looks_like_recipe_title_candidate() — tests a clean standalone line
  2. _extract_recipe_title_prefix_from_run_on_line() — extracts a title prefix
     when the title and ingredient list are on the same line (run-on format)

Both use heuristics rather than an LLM because:
  - Title extraction runs on every chunk at upload time (~100s to 1000s of chunks)
  - An LLM call per chunk would cost orders of magnitude more
  - Heuristics achieve ~90% accuracy on tested cookbooks — good enough for
    a display label, not a semantic contract

The constant sets at the top of this module (_NON_RECIPE_DETECTED_TITLES,
_RECIPE_TITLE_STOPWORDS, etc.) encode the heuristic rules as named data,
making them easy to extend without modifying function logic.
"""

import base64
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from sqlalchemy import delete
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.core.rate_limit import limiter
from app.models.enums import ChunkType, IngestionStatus
from app.models.ingestion import BookRecord, IngestionJob
router = APIRouter(prefix="/ingest")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB — generous for full cookbooks

# These are section headings that appear in cookbook PDFs, not recipe titles.
# If the extracted title starts with one of these, the chunk is front matter
# or navigation content, not a recipe.
_NON_RECIPE_DETECTED_TITLES = (
    "index",
    "introduction",
    "foreword",
    "preface",
    "contents",
)

# Words that would indicate we're reading an instruction or section header,
# not a recipe title. A chunk whose first line is "Ingredients" or "Method"
# doesn't have a title — it starts mid-recipe or is a malformed chunk.
_RECIPE_TITLE_STOPWORDS = {
    "ingredients",
    "ingredient",
    "method",
    "directions",
    "direction",
    "preparation",
    "preparation.",
    "instructions",
    "notes",
    "note",
    "serves",
    "yield",
}

# Words that can appear IN a recipe title (connectors) but cannot constitute
# the majority of it. "Chicken with Lemon" is valid; "With and In" is not.
_RECIPE_TITLE_CONNECTORS = {"with", "and", "or", "in", "with.", "style", "sauce"}

# Title length gates — titles shorter than 2 words are ambiguous (single words
# can be chapter headings), titles longer than 10 words are likely instructions.
_RECIPE_TITLE_MIN_WORDS = 2
_RECIPE_TITLE_MAX_WORDS = 10
_RECIPE_TITLE_MAX_CHARS = 160

# Run-on line detection: if a measure word appears within the first 5 tokens
# of a line, the line is likely an ingredient list, not a title run-on.
# e.g. "1 cup flour" → pure ingredient, no title prefix to extract.
_RECIPE_TITLE_MAX_TOKENS_BEFORE_MEASURE = 5

# Measurement words that signal the transition from title to ingredient list
# in run-on lines. "Roast Chicken 1 whole chicken" → boundary is before "1".
_RECIPE_TITLE_MEASURE_WORDS = {
    "cup",
    "cups",
    "tablespoon",
    "tablespoons",
    "tbsp",
    "teaspoon",
    "teaspoons",
    "tsp",
    "pound",
    "pounds",
    "lb",
    "lbs",
    "ounce",
    "ounces",
    "oz",
    "clove",
    "cloves",
    "can",
    "cans",
    "quart",
    "quarts",
    "pint",
    "pints",
    "slice",
    "slices",
    "egg",
    "eggs",
    "ear",
    "ears",
    "package",
    "packages",
    "head",
    "heads",
}

# Adjectives that describe ingredients but never appear at the start of a recipe
# title. "Fresh Herb Salad" is valid but "Fresh 2 tbsp..." means "fresh" is
# modifying an ingredient quantity that starts a run-on line, not a title word.
_RECIPE_TITLE_LEADING_INGREDIENTS = {
    "small",
    "medium",
    "large",
    "fresh",
    "hot",
    "cold",
    "lean",
    "dried",
    "ground",
    "grated",
    "minced",
    "chopped",
    "sliced",
    "melted",
    "beaten",
    "cooked",
    "peeled",
    "diced",
    "whole",
    "white",
    "brown",
    "green",
    "red",
    "yellow",
    "baking",
    "dozen",
}


def _normalise_detected_recipe_line(line: str) -> str:
    """Collapse internal whitespace and strip leading/trailing noise characters.

    Cookbook OCR produces lines with variable internal spaces (column alignment
    artifacts) and leading/trailing punctuation artifacts. This normalises both
    before any heuristic testing.
    """
    cleaned = re.sub(r"\s+", " ", line).strip(" \t-–—•*.:;,")
    return cleaned


def _first_meaningful_line(text: str) -> str:
    """Return the first non-empty normalised line from chunk text, capped at max chars.

    Used as a fallback when none of the first 4 lines yields a title candidate —
    we still try the very first line of the chunk before giving up and generating
    a synthetic "Recipe on page N" title.
    """
    for line in text.splitlines():
        stripped = _normalise_detected_recipe_line(line)
        if stripped:
            return stripped[:_RECIPE_TITLE_MAX_CHARS]
    return ""


def _looks_like_detected_recipe_noise(chunk_text: str, recipe_name: str, chapter: str | None = None) -> bool:
    """Determine whether a chunk is front matter, an index page, or other non-recipe content.

    Called after state machine classification — the state machine may label a chunk
    RECIPE because it contains ingredient-like lines, even if the chunk is actually
    an index page listing "Chicken Soup ... page 42". This function provides a
    second-pass rejection.

    Signal counting approach: we count line types rather than making a single
    binary decision. A real recipe page has ingredient lines and/or narrative
    recipe signals. An index page has many page-reference lines, index entries,
    and embedded page numbers but no ingredient amounts.

    The 'chapter' guard short-circuits for known front-matter chapter names —
    if pdfplumber detected the chapter heading as "index" or "contents", we
    skip further analysis and reject immediately.
    """
    lowered_name = recipe_name.strip().lower()
    lowered_chapter = (chapter or "").strip().lower()

    # Fast reject: title starts with a known non-recipe prefix
    if any(lowered_name.startswith(prefix) for prefix in _NON_RECIPE_DETECTED_TITLES):
        return True

    lowered_text = chunk_text.lower()

    # Corpus-specific noise patterns found during testing on Southern Cook Book scans.
    # These are heuristics that caught false positives in the test set — not general rules.
    if "index page" in lowered_text or lowered_text.startswith("index "):
        return True
    if lowered_text.startswith("introduction ") and "cook book" in lowered_text:
        return True
    if lowered_text.count("the southern cook book") >= 2:
        return True

    lines = [_normalise_detected_recipe_line(line) for line in chunk_text.splitlines()]
    nonempty_lines = [line for line in lines if line]

    # Per-line signal counters — tallied for threshold decisions below
    page_marker_lines = 0      # lines containing "page" or bare page numbers
    index_entry_lines = 0      # lines that look like "Recipe Name   42"
    narrative_recipe_signals = 0  # lines with "serves", "method", etc.
    amount_lines = 0           # lines starting with a quantity + unit
    embedded_number_lines = 0  # lines with multiple bare numbers (page refs)
    catalog_prose_lines = 0    # lines with "index", "catalog", etc.

    for line in nonempty_lines:
        lowered_line = line.lower()
        digit_count = len(re.findall(r"\b\d{1,3}\b", line))

        # Page marker: explicit "page" word or bare number line
        if re.search(r"\bpage\b", lowered_line):
            page_marker_lines += 1
        if re.fullmatch(r"(?:page\s+)?\d{1,3}", lowered_line):
            page_marker_lines += 1

        # Index entry pattern: "Some Recipe Name  42" — text ending in a single number
        # Exclude ingredient lines that start with a quantity.
        if (
            re.search(r"[a-z]", line)
            and re.search(r"\b\d{1,3}\b\s*$", line)
            and digit_count == 1
            and not re.match(
                r"^(?:\d+|a|an)\s+(cup|cups|tablespoon|tablespoons|tbsp|teaspoon|teaspoons|tsp|pound|pounds|lb|lbs|ounce|ounces|oz|clove|cloves)\b",
                lowered_line,
            )
        ):
            index_entry_lines += 1

        # Embedded number lines: multiple bare numbers on one line suggest a table
        # or index column, not ingredient lists (which have a leading quantity + unit).
        if digit_count >= 2 and not re.match(
            r"^(?:\d+|a|an)\s+(cup|cups|tablespoon|tablespoons|tbsp|teaspoon|teaspoons|tsp|pound|pounds|lb|lbs|ounce|ounces|oz|clove|cloves)\b",
            lowered_line,
        ):
            embedded_number_lines += 1

        # Amount lines: lines starting with "2 cups" or "a tablespoon of" — real ingredient lines
        if re.match(
            r"^(?:\d+|a|an)\s+(cup|cups|tablespoon|tablespoons|tbsp|teaspoon|teaspoons|tsp|pound|pounds|lb|lbs|ounce|ounces|oz|clove|cloves|can|cans|quart|quarts|pint|pints|slice|slices|egg|eggs|ear|ears|package|packages|head|heads)\b",
            lowered_line,
        ):
            amount_lines += 1

        if re.search(r"\b(serves|yield|ingredients|method|directions|instructions)\b", lowered_line):
            narrative_recipe_signals += 1
        if re.search(r"\b(index|catalog|entry|continued|see also)\b", lowered_line):
            catalog_prose_lines += 1

    # Global page density check: many "page" occurrences + many bare numbers
    # across the whole chunk is a strong index/front-matter signal.
    if len(re.findall(r"\bpage\b", lowered_text)) >= 3 and len(re.findall(r"\b\d{1,3}\b", lowered_text)) >= 8:
        return True

    # has_recipe_body: at least one quantity line or recipe keyword → real recipe content
    # Used to grant exceptions to the noise heuristics below.
    has_recipe_body = amount_lines >= 1 or narrative_recipe_signals >= 1

    # Chapter-level guards: known front-matter chapters with no recipe body
    if lowered_chapter in {"index", "contents", "introduction", "foreword", "preface"} and not has_recipe_body:
        return True

    # Structural noise patterns: multiple page marker lines with no recipe content
    if page_marker_lines >= 3 and not has_recipe_body:
        return True
    if index_entry_lines >= 2 and not has_recipe_body:
        return True
    if page_marker_lines >= 2 and embedded_number_lines >= 1 and not has_recipe_body:
        return True
    if page_marker_lines >= 2 and catalog_prose_lines >= 1 and not has_recipe_body:
        return True

    return False


def _looks_like_recipe_title_candidate(candidate: str) -> bool:
    """Decide whether a clean standalone line looks like a recipe title.

    Called on:
    1. The first 4 lines of a chunk (standalone line path in _extract_detected_recipe_name)
    2. The extracted prefix from a run-on line (_extract_recipe_title_prefix_from_run_on_line)

    Approach: fast-fail via negative rules (cheaper) before positive confirmation.
    The function accumulates rejections rather than looking for one positive signal —
    recipe titles are hard to define positively, but easy to reject with heuristics.

    Title length bounds:
      - < 2 alpha words: ambiguous (single words are chapter headings, single letters
        are section markers)
      - > 10 words: almost certainly a sentence or instruction, not a title
    """
    if not candidate:
        return False

    lowered = candidate.lower().strip()

    # Structural prefixes that indicate this is navigation/front matter
    if any(lowered.startswith(prefix) for prefix in _NON_RECIPE_DETECTED_TITLES):
        return False

    # Structural terms that indicate chapter/section context, not recipe titles
    if any(marker in lowered for marker in ("index page", "chapter ", "menu ", "copyright", "appendix")):
        return False

    # Lines starting with digits or fractions are ingredient quantities, not titles.
    # "2/3 cup sugar" is an ingredient line, not a title called "2/3 cup sugar".
    if re.match(r"^\d+(?:[\/.-]\d+)?\s", lowered):
        return False

    # Lines starting with a quantity + unit are ingredient lines
    if re.match(
        r"^(?:\d+|a|an)\s+(cup|cups|tablespoon|tablespoons|tbsp|teaspoon|teaspoons|tsp|pound|pounds|lb|lbs|ounce|ounces|oz|clove|cloves)\b",
        lowered,
    ):
        return False

    words = [word for word in re.split(r"\s+", lowered) if word]
    if not words or len(words) > _RECIPE_TITLE_MAX_WORDS:
        return False

    if len(words) < _RECIPE_TITLE_MIN_WORDS:
        return False

    # Must have at least 2 words containing alphabetic characters.
    # "42 37" passes the word count check but is clearly not a title.
    alpha_words = [word for word in words if re.search(r"[a-z]", word)]
    if len(alpha_words) < _RECIPE_TITLE_MIN_WORDS:
        return False

    # First word is a stopword → this is a section header, not a recipe name
    if words[0] in _RECIPE_TITLE_STOPWORDS:
        return False

    # All words are stopwords → not a meaningful title
    if all(word in _RECIPE_TITLE_STOPWORDS for word in words):
        return False

    # Connector ratio check: a title can have connectors ("Chicken with Lemon")
    # but a phrase like "with and in" that's mostly connectors is not a title.
    if len(alpha_words) >= 3:
        connector_count = sum(1 for word in alpha_words if word in _RECIPE_TITLE_CONNECTORS)
        if connector_count >= max(2, len(alpha_words) - 1):
            return False

    # Sentence-like markers: real titles don't contain cooking instructions.
    # "Bake until golden then serve with sauce" is a method step, not a title.
    sentence_like_markers = (" then ", " until ", " minutes", " hour", " oven", " stir ", " bake ", " combine ")
    if any(marker in lowered for marker in sentence_like_markers):
        return False

    return True


def _extract_recipe_title_prefix_from_run_on_line(line: str) -> str:
    """Extract a recipe title prefix from a line where title and ingredients run together.

    Many cookbook PDFs (especially historical OCR scans) format recipe titles
    with no line break before the ingredient list:
      "Roast Chicken 1 whole chicken, 3 tablespoons olive oil..."

    This function finds the boundary between the title and the ingredient list
    by detecting the first measure word (tablespoon, cup, pound, etc.) and
    working backwards to find where the title ends.

    Boundary detection algorithm:
      1. Scan tokens for the first measure word (e.g. "tablespoon").
      2. If the preceding token is numeric (e.g. "3"), step back to before it —
         that's the ingredient quantity, not part of the title.
      3. Strip trailing ingredient adjectives (_RECIPE_TITLE_LEADING_INGREDIENTS)
         from the candidate — "Fresh 2 tablespoons butter" should not yield "Fresh".
      4. Validate the candidate against _looks_like_recipe_title_candidate().

    Second pass (no measure word found):
      If no measure word is found, look for the first purely numeric token.
      If it's followed by an ingredient adjective + lowercase word, that's a
      run-on quantity line and the tokens before it are the title.

    Returns empty string if no valid title prefix is found — the caller falls back
    to generating "Recipe on page N".
    """
    tokens = [token for token in re.split(r"\s+", line.strip()) if token]
    if len(tokens) < _RECIPE_TITLE_MIN_WORDS:
        return ""

    def _normalise_token(token: str) -> str:
        return re.sub(r"[^a-z]", "", token.lower())

    def _is_numericish(token: str) -> bool:
        # Matches digits, fractions, measurement abbreviations
        return bool(re.fullmatch(r"[\d%.,^½¼¾⅓⅔⅛⅜⅝⅞/¥*#-]+", token))

    def _looks_like_ingredient_lead(token: str) -> bool:
        normalised = _normalise_token(token)
        return bool(normalised) and normalised in _RECIPE_TITLE_LEADING_INGREDIENTS

    def _looks_like_lowercase_ingredient(token: str) -> bool:
        # A token starting with a lowercase letter is likely a continuation of
        # an ingredient quantity ("2 tablespoons butter" → "butter" is lowercase)
        if not re.search(r"[A-Za-z]", token):
            return False
        stripped = re.sub(r"[^A-Za-z]", "", token)
        return bool(stripped) and stripped[0].islower()

    def _trim_candidate_tokens(candidate_tokens: list[str]) -> list[str]:
        """Strip trailing numeric or ingredient-adjective tokens from a candidate."""
        trimmed = candidate_tokens[:]
        while trimmed:
            trailing = _normalise_token(trimmed[-1])
            if trailing.isdigit() or trailing in _RECIPE_TITLE_LEADING_INGREDIENTS:
                trimmed.pop()
                continue
            if _is_numericish(trimmed[-1]):
                trimmed.pop()
                continue
            break
        return trimmed

    def _contains_numeric_or_ingredient_spill(candidate_tokens: list[str]) -> bool:
        """True if any token in the candidate is a numeric/ingredient spill-over."""
        return any(
            _is_numericish(token)
            or _looks_like_ingredient_lead(token)
            or _looks_like_lowercase_ingredient(token)
            for token in candidate_tokens
        )

    # First pass: scan for measure words (tablespoon, cup, pound, etc.)
    for idx, token in enumerate(tokens):
        normalised = _normalise_token(token)
        # Only act on measure words that aren't the first token
        # (a line starting with "cups" is a quantity line, not a run-on title)
        if normalised not in _RECIPE_TITLE_MEASURE_WORDS or idx == 0:
            continue

        # Step back over the preceding quantity token (e.g. "3" in "3 tablespoons")
        boundary = idx
        if re.fullmatch(r"[\divxlcdmIVXLCDM/%.,^½¼¾]+", tokens[idx - 1]):
            boundary = idx - 1

        # Continue stepping back over ingredient adjectives and numeric tokens
        lookback = boundary
        while lookback > 0 and (_is_numericish(tokens[lookback - 1]) or _looks_like_ingredient_lead(tokens[lookback - 1])):
            lookback -= 1

        # If there's a lowercase ingredient word just before the quantity, step back past it
        if lookback > 0 and _looks_like_lowercase_ingredient(tokens[lookback - 1]):
            boundary = lookback - 1
        else:
            boundary = lookback

        if boundary <= 0:
            return ""

        candidate_tokens = _trim_candidate_tokens(tokens[:boundary])
        if not candidate_tokens:
            return ""

        # If any candidate token looks like an ingredient spill, this measure word
        # boundary didn't cleanly separate title from ingredients — try the next one.
        if _contains_numeric_or_ingredient_spill(candidate_tokens):
            continue

        if len(candidate_tokens) > _RECIPE_TITLE_MAX_WORDS:
            candidate_tokens = candidate_tokens[:_RECIPE_TITLE_MAX_WORDS]

        candidate = " ".join(candidate_tokens)
        candidate = _normalise_detected_recipe_line(candidate)
        if not candidate:
            return ""

        # Single-word title: accept if it contains letters (handles "Soup", "Cake" etc.)
        if len(candidate.split()) == 1 and re.search(r"[A-Za-z]", candidate):
            return candidate[:_RECIPE_TITLE_MAX_CHARS]

        if _looks_like_recipe_title_candidate(candidate):
            return candidate[:_RECIPE_TITLE_MAX_CHARS]
        return ""

    # Second pass: no measure word found — look for first numeric token
    # Pattern: "Recipe Name 2 large eggs..." → first numeric is "2", title is "Recipe Name"
    first_numericish_index = next((i for i, token in enumerate(tokens) if _is_numericish(token)), None)
    if first_numericish_index is not None and first_numericish_index >= _RECIPE_TITLE_MIN_WORDS:
        boundary = first_numericish_index
        # Skip over multiple consecutive numeric tokens ("2 1/2 cups")
        while boundary < len(tokens) and _is_numericish(tokens[boundary]):
            boundary += 1
        # Skip ingredient adjectives after the number
        while boundary < len(tokens) and _looks_like_ingredient_lead(tokens[boundary]):
            boundary += 1
        # If followed by a lowercase ingredient word, the pre-numeric tokens are the title
        if boundary < len(tokens) and _looks_like_lowercase_ingredient(tokens[boundary]):
            candidate_tokens = _trim_candidate_tokens(tokens[:first_numericish_index])
            if candidate_tokens:
                candidate = _normalise_detected_recipe_line(" ".join(candidate_tokens))
                if candidate and _looks_like_recipe_title_candidate(candidate):
                    return candidate[:_RECIPE_TITLE_MAX_CHARS]

    return ""


def _extract_detected_recipe_name(text: str, page_number: int | None) -> str:
    """Extract a recipe name from the first lines of a chunk.

    Tries two strategies on each of the first 4 lines:
      1. Run-on line extraction: title prefix before the ingredient list
      2. Standalone line: the whole line is the title

    Falls back to the first meaningful line, then to "Recipe on page N".

    Why the first 4 lines?
      Recipe titles appear at the start of a chunk — if we haven't found a
      title by line 4, the chunk probably starts mid-recipe (no title visible
      in this page scan) or is a recipe with a very unusual format. After 4
      lines we'd be testing ingredient or method lines, which will almost
      always fail the heuristics.
    """
    lines = [_normalise_detected_recipe_line(line) for line in text.splitlines()]
    nonempty_lines = [line for line in lines if line]

    # Try each of the first 4 non-empty lines
    for line in nonempty_lines[:4]:
        # Try run-on extraction first (handles "Roast Chicken 1 whole chicken...")
        if title_prefix := _extract_recipe_title_prefix_from_run_on_line(line):
            return title_prefix
        # Try treating the whole line as a standalone title
        if _looks_like_recipe_title_candidate(line):
            return line[:_RECIPE_TITLE_MAX_CHARS]

    # Fallback: re-examine the very first line (may have been excluded from nonempty_lines
    # if normalisation produced an empty string — rare but possible)
    first_line = _first_meaningful_line(text)
    if title_prefix := _extract_recipe_title_prefix_from_run_on_line(first_line):
        return title_prefix
    if first_line and _looks_like_recipe_title_candidate(first_line):
        return first_line[:_RECIPE_TITLE_MAX_CHARS]

    # Ultimate fallback: synthetic title with page number so the chunk is
    # identifiable in the admin UI even when title extraction fails.
    return f"Recipe on page {page_number or '—'}"


@router.post("", status_code=202)
@limiter.limit("10/hour")
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    db: DBSession = ...,  # type: ignore[assignment]
    current_user: CurrentUser = ...,  # type: ignore[assignment]
):
    """
    INTERNAL/ADMIN: Upload a PDF for curated cookbook ingestion.

    Returns job_id immediately (202 Accepted). Background processing via Celery.
    This endpoint is not exposed in the active product UI (M015 pivot).
    Future use: admin-only curated library management.

    Why 202 instead of 201?
      202 Accepted signals that the request was accepted for processing but
      hasn't been processed yet. The caller must poll GET /ingest/{job_id}
      to check completion. 201 would imply the resource exists, which it
      doesn't — the cookbook chunks don't exist until the Celery task finishes.

    PDF bytes → base64 for Celery:
      Celery serialises task arguments as JSON (the default broker_serializer).
      Raw bytes aren't JSON-serialisable, so we base64-encode them before
      enqueuing. The task decodes them back before passing to the ingestion
      pipeline. For very large PDFs, a V2 improvement would store to object
      storage (S3/GCS) and pass a reference URL instead — avoiding the
      base64 overhead and Celery message size limits.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")

    content = await file.read()

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

    # Create the IngestionJob record before enqueuing so we have a job_id to return.
    # The Celery task updates this record with progress and final status.
    # book_statuses is a JSON list — one entry per book in the upload (always 1 here).
    job = IngestionJob(
        user_id=current_user.user_id,
        status=IngestionStatus.PENDING,
        book_count=1,
        book_statuses=[{"title": file.filename, "status": "pending"}],
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue ingestion task — base64-encode PDF bytes for JSON serialiser.
    # TODO: V2 should store to object storage and pass a reference instead.
    # Late import to avoid circular dependency (tasks → celery_app → settings → db).
    from app.workers.tasks import ingest_cookbook

    content_b64 = base64.b64encode(content).decode("ascii")
    result = ingest_cookbook.delay(str(job.job_id), str(current_user.user_id), content_b64, file.filename)  # type: ignore[attr-defined]

    # Store celery_task_id so the cancel endpoint can revoke the task if needed.
    job.celery_task_id = result.id
    db.add(job)
    await db.commit()

    return {"job_id": str(job.job_id)}


@router.get("/{job_id}")
async def get_ingestion_status(job_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """
    INTERNAL/ADMIN: Poll ingestion job status.

    Returns the IngestionJob row directly — includes status, book_statuses
    (per-book progress), failed count, and celery_task_id.

    Ownership check: user_id must match the job's owner. Unlike session routes
    there's no admin override here — admins use the same upload flow as each
    other and can only view their own jobs. A separate admin overview endpoint
    would be added if needed.

    This endpoint is not exposed in the active product UI (M015 pivot).
    """
    job = await db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    if job.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return job


@router.post("/{job_id}/cancel", status_code=200)
async def cancel_ingestion(job_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """
    INTERNAL/ADMIN: Cancel an in-progress ingestion job.

    Two-step cancellation:
      1. Celery revoke with terminate=True + SIGTERM: asks the worker to stop
         the running task. terminate=True sends the signal even if the task
         has already started (not just queued). SIGTERM gives the task a
         chance to clean up — SIGKILL would leave the DB in an inconsistent state.
      2. Mark job as FAILED in the DB: even if the Celery revoke fails (worker
         already finished or network issue), the job status is correctly set.
         The bare except passes — revocation failure doesn't block the status update.

    Why mark as FAILED, not CANCELLED?
      IngestionStatus has no CANCELLED state. FAILED is the correct terminal
      state for an incomplete job. The book_statuses entry is updated with
      error="Upload cancelled by user." to distinguish user cancellation from
      pipeline failures in the admin UI.

    409 Conflict for already-terminal jobs: attempting to cancel a COMPLETE
    or FAILED job has no meaningful effect — the task is gone. 409 communicates
    "the state transition you requested is not valid right now" more clearly
    than a silent no-op.

    This endpoint is not exposed in the active product UI (M015 pivot).
    """
    job = await db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    if job.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if job.status in {IngestionStatus.COMPLETE, IngestionStatus.FAILED}:
        raise HTTPException(status_code=409, detail=f"Ingestion job is already {job.status}")

    if job.celery_task_id:
        try:
            from app.workers.celery_app import celery_app

            celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception:
            # Revocation failure doesn't block the status update.
            # The task may have already completed or the broker may be unreachable.
            pass

    # Mark terminal state regardless of whether Celery revocation succeeded.
    job.status = IngestionStatus.FAILED
    job.failed = max(job.failed, 1)  # Ensure failed count is at least 1
    job.completed_at = job.completed_at or datetime.now(timezone.utc).replace(tzinfo=None)

    # Preserve existing book metadata (title etc.) while updating status and error.
    existing = job.book_statuses[0] if job.book_statuses else {"title": "Cookbook upload"}
    job.book_statuses = [
        {
            **existing,
            "status": "failed",
            "phase": "failed",
            "error": "Upload cancelled by user.",
        }
    ]
    db.add(job)
    await db.commit()
    return {"job_id": str(job.job_id), "status": "failed", "message": "Ingestion cancelled"}
