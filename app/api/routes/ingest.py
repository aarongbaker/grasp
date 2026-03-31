"""api/routes/ingest.py — PDF upload and ingestion job polling."""

import base64
import re
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from sqlalchemy import delete
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.models.enums import ChunkType, IngestionStatus
from app.models.ingestion import BookRecord, IngestionJob

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/ingest")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_NON_RECIPE_DETECTED_TITLES = (
    "index",
    "introduction",
    "foreword",
    "preface",
    "contents",
)
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
_RECIPE_TITLE_CONNECTORS = {"with", "and", "or", "in", "with.", "style", "sauce"}
_RECIPE_TITLE_MIN_WORDS = 2
_RECIPE_TITLE_MAX_WORDS = 10
_RECIPE_TITLE_MAX_CHARS = 160
_RECIPE_TITLE_MAX_TOKENS_BEFORE_MEASURE = 5
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
    cleaned = re.sub(r"\s+", " ", line).strip(" \t-–—•*.:;,")
    return cleaned


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = _normalise_detected_recipe_line(line)
        if stripped:
            return stripped[:_RECIPE_TITLE_MAX_CHARS]
    return ""


def _looks_like_detected_recipe_noise(chunk_text: str, recipe_name: str, chapter: str | None = None) -> bool:
    lowered_name = recipe_name.strip().lower()
    lowered_chapter = (chapter or "").strip().lower()
    if any(lowered_name.startswith(prefix) for prefix in _NON_RECIPE_DETECTED_TITLES):
        return True

    lowered_text = chunk_text.lower()
    if "index page" in lowered_text or lowered_text.startswith("index "):
        return True
    if lowered_text.startswith("introduction ") and "cook book" in lowered_text:
        return True
    if lowered_text.count("the southern cook book") >= 2:
        return True

    lines = [_normalise_detected_recipe_line(line) for line in chunk_text.splitlines()]
    nonempty_lines = [line for line in lines if line]
    page_marker_lines = 0
    index_entry_lines = 0
    narrative_recipe_signals = 0
    amount_lines = 0
    embedded_number_lines = 0
    catalog_prose_lines = 0

    for line in nonempty_lines:
        lowered_line = line.lower()
        digit_count = len(re.findall(r"\b\d{1,3}\b", line))

        if re.search(r"\bpage\b", lowered_line):
            page_marker_lines += 1
        if re.fullmatch(r"(?:page\s+)?\d{1,3}", lowered_line):
            page_marker_lines += 1

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

        if digit_count >= 2 and not re.match(
            r"^(?:\d+|a|an)\s+(cup|cups|tablespoon|tablespoons|tbsp|teaspoon|teaspoons|tsp|pound|pounds|lb|lbs|ounce|ounces|oz|clove|cloves)\b",
            lowered_line,
        ):
            embedded_number_lines += 1

        if re.match(
            r"^(?:\d+|a|an)\s+(cup|cups|tablespoon|tablespoons|tbsp|teaspoon|teaspoons|tsp|pound|pounds|lb|lbs|ounce|ounces|oz|clove|cloves|can|cans|quart|quarts|pint|pints|slice|slices|egg|eggs|ear|ears|package|packages|head|heads)\b",
            lowered_line,
        ):
            amount_lines += 1

        if re.search(r"\b(serves|yield|ingredients|method|directions|instructions)\b", lowered_line):
            narrative_recipe_signals += 1
        if re.search(r"\b(index|catalog|entry|continued|see also)\b", lowered_line):
            catalog_prose_lines += 1

    if len(re.findall(r"\bpage\b", lowered_text)) >= 3 and len(re.findall(r"\b\d{1,3}\b", lowered_text)) >= 8:
        return True

    has_recipe_body = amount_lines >= 1 or narrative_recipe_signals >= 1
    if lowered_chapter in {"index", "contents", "introduction", "foreword", "preface"} and not has_recipe_body:
        return True
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
    if not candidate:
        return False

    lowered = candidate.lower().strip()
    if any(lowered.startswith(prefix) for prefix in _NON_RECIPE_DETECTED_TITLES):
        return False

    if any(marker in lowered for marker in ("index page", "chapter ", "menu ", "copyright", "appendix")):
        return False

    if re.match(r"^\d+(?:[\/.-]\d+)?\s", lowered):
        return False
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

    alpha_words = [word for word in words if re.search(r"[a-z]", word)]
    if len(alpha_words) < _RECIPE_TITLE_MIN_WORDS:
        return False

    if words[0] in _RECIPE_TITLE_STOPWORDS:
        return False

    if all(word in _RECIPE_TITLE_STOPWORDS for word in words):
        return False

    if len(alpha_words) >= 3:
        connector_count = sum(1 for word in alpha_words if word in _RECIPE_TITLE_CONNECTORS)
        if connector_count >= max(2, len(alpha_words) - 1):
            return False

    sentence_like_markers = (" then ", " until ", " minutes", " hour", " oven", " stir ", " bake ", " combine ")
    if any(marker in lowered for marker in sentence_like_markers):
        return False

    return True


def _extract_recipe_title_prefix_from_run_on_line(line: str) -> str:
    tokens = [token for token in re.split(r"\s+", line.strip()) if token]
    if len(tokens) < _RECIPE_TITLE_MIN_WORDS:
        return ""

    def _normalise_token(token: str) -> str:
        return re.sub(r"[^a-z]", "", token.lower())

    def _is_numericish(token: str) -> bool:
        return bool(re.fullmatch(r"[\d%.,^½¼¾⅓⅔⅛⅜⅝⅞/¥*#-]+", token))

    def _looks_like_ingredient_lead(token: str) -> bool:
        normalised = _normalise_token(token)
        return bool(normalised) and normalised in _RECIPE_TITLE_LEADING_INGREDIENTS

    def _looks_like_lowercase_ingredient(token: str) -> bool:
        if not re.search(r"[A-Za-z]", token):
            return False
        stripped = re.sub(r"[^A-Za-z]", "", token)
        return bool(stripped) and stripped[0].islower()

    for idx, token in enumerate(tokens):
        normalised = _normalise_token(token)
        if normalised not in _RECIPE_TITLE_MEASURE_WORDS or idx == 0:
            continue

        boundary = idx
        if re.fullmatch(r"[\divxlcdmIVXLCDM/%.,^½¼¾]+", tokens[idx - 1]):
            boundary = idx - 1

        lookback = boundary
        while lookback > 0 and (_is_numericish(tokens[lookback - 1]) or _looks_like_ingredient_lead(tokens[lookback - 1])):
            lookback -= 1

        if lookback > 0 and _looks_like_lowercase_ingredient(tokens[lookback - 1]):
            boundary = lookback - 1
        else:
            boundary = lookback

        if boundary <= 0:
            return ""

        candidate_tokens = tokens[:boundary]
        while candidate_tokens:
            trailing = _normalise_token(candidate_tokens[-1])
            if trailing.isdigit() or trailing in _RECIPE_TITLE_LEADING_INGREDIENTS:
                candidate_tokens.pop()
                continue
            if _is_numericish(candidate_tokens[-1]):
                candidate_tokens.pop()
                continue
            break

        if not candidate_tokens:
            return ""

        if len(candidate_tokens) > _RECIPE_TITLE_MAX_WORDS:
            candidate_tokens = candidate_tokens[:_RECIPE_TITLE_MAX_WORDS]

        candidate = " ".join(candidate_tokens)
        candidate = _normalise_detected_recipe_line(candidate)
        if not candidate:
            return ""
        if len(candidate.split()) == 1 and re.search(r"[A-Za-z]", candidate):
            return candidate[:_RECIPE_TITLE_MAX_CHARS]
        if _looks_like_recipe_title_candidate(candidate):
            return candidate[:_RECIPE_TITLE_MAX_CHARS]
        return ""
    return ""


def _extract_detected_recipe_name(text: str, page_number: int | None) -> str:
    lines = [_normalise_detected_recipe_line(line) for line in text.splitlines()]
    nonempty_lines = [line for line in lines if line]

    for line in nonempty_lines[:4]:
        if title_prefix := _extract_recipe_title_prefix_from_run_on_line(line):
            return title_prefix
        if _looks_like_recipe_title_candidate(line):
            return line[:_RECIPE_TITLE_MAX_CHARS]

    first_line = _first_meaningful_line(text)
    if title_prefix := _extract_recipe_title_prefix_from_run_on_line(first_line):
        return title_prefix
    if first_line and _looks_like_recipe_title_candidate(first_line):
        return first_line[:_RECIPE_TITLE_MAX_CHARS]

    return f"Recipe on page {page_number or '—'}"


@router.post("", status_code=202)
@limiter.limit("10/hour")
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    db: DBSession = ...,
    current_user: CurrentUser = ...,
):
    """Upload a PDF. Returns job_id immediately. Background processing via Celery."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")

    content = await file.read()

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

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
    from app.workers.tasks import ingest_cookbook

    content_b64 = base64.b64encode(content).decode("ascii")
    ingest_cookbook.delay(str(job.job_id), str(current_user.user_id), content_b64, file.filename)

    return {"job_id": str(job.job_id)}


@router.get("/cookbooks")
async def list_cookbooks(db: DBSession, current_user: CurrentUser):
    """Returns all ingested cookbooks for the current user, newest first."""
    statement = (
        select(BookRecord).where(BookRecord.user_id == current_user.user_id).order_by(BookRecord.created_at.desc())
    )
    results = await db.exec(statement)
    books = results.all()

    def _document_type_value(value):
        if value is None:
            return None
        return getattr(value, "value", value)

    return [
        {
            "book_id": str(b.book_id),
            "title": b.title,
            "author": b.author,
            "document_type": _document_type_value(b.document_type),
            "total_pages": b.total_pages,
            "total_chunks": b.total_chunks,
            "created_at": b.created_at.isoformat(),
        }
        for b in books
    ]


@router.get("/detected-recipes")
async def list_detected_recipes(db: DBSession, current_user: CurrentUser):
    """Returns recipe-like cookbook chunks for the current user, grouped by source book metadata."""
    from app.models.ingestion import CookbookChunk

    statement = (
        select(CookbookChunk, BookRecord)
        .join(BookRecord, CookbookChunk.book_id == BookRecord.book_id)
        .where(CookbookChunk.user_id == current_user.user_id)
        .where(CookbookChunk.chunk_type == ChunkType.RECIPE)
        .order_by(BookRecord.created_at.desc(), CookbookChunk.page_number.asc(), CookbookChunk.created_at.asc())
    )
    results = await db.exec(statement)
    rows = results.all()

    return [
        {
            "chunk_id": str(chunk.chunk_id),
            "book_id": str(book.book_id),
            "book_title": book.title,
            "recipe_name": recipe_name,
            "chapter": chunk.chapter,
            "page_number": chunk.page_number,
            "text": chunk.text,
        }
        for chunk, book in rows
        if not _looks_like_detected_recipe_noise(
            chunk.text,
            (recipe_name := _extract_detected_recipe_name(chunk.text, chunk.page_number)),
            chunk.chapter,
        )
    ]


@router.delete("/cookbooks/{book_id}", status_code=204)
async def delete_cookbook(book_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """Delete a cookbook and its associated chunk/page metadata for the current user."""
    from app.models.ingestion import CookbookChunk, PageCache

    book = await db.get(BookRecord, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Cookbook not found")
    if book.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    chunk_results = await db.exec(select(CookbookChunk).where(CookbookChunk.book_id == book_id))
    chunks = chunk_results.all()
    vector_ids = [str(chunk.chunk_id) for chunk in chunks]

    await db.exec(delete(CookbookChunk).where(CookbookChunk.book_id == book_id))
    await db.exec(delete(PageCache).where(PageCache.book_id == book_id))
    await db.exec(delete(BookRecord).where(BookRecord.book_id == book_id))
    await db.commit()

    if vector_ids:
        try:
            from app.workers.tasks import delete_cookbook_vectors

            delete_cookbook_vectors.delay(str(book_id), vector_ids)
        except Exception:
            # Relational delete already succeeded. Vector cleanup is best-effort.
            pass


@router.get("/{job_id}")
async def get_ingestion_status(job_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    job = await db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    if job.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return job
