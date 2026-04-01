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
"""

import base64
import re
import uuid
from datetime import datetime, timezone

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

    def _trim_candidate_tokens(candidate_tokens: list[str]) -> list[str]:
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
        return any(
            _is_numericish(token)
            or _looks_like_ingredient_lead(token)
            or _looks_like_lowercase_ingredient(token)
            for token in candidate_tokens
        )

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

        candidate_tokens = _trim_candidate_tokens(tokens[:boundary])
        if not candidate_tokens:
            return ""
        if _contains_numeric_or_ingredient_spill(candidate_tokens):
            continue

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

    first_numericish_index = next((i for i, token in enumerate(tokens) if _is_numericish(token)), None)
    if first_numericish_index is not None and first_numericish_index >= _RECIPE_TITLE_MIN_WORDS:
        boundary = first_numericish_index
        while boundary < len(tokens) and _is_numericish(tokens[boundary]):
            boundary += 1
        while boundary < len(tokens) and _looks_like_ingredient_lead(tokens[boundary]):
            boundary += 1
        if boundary < len(tokens) and _looks_like_lowercase_ingredient(tokens[boundary]):
            candidate_tokens = _trim_candidate_tokens(tokens[:first_numericish_index])
            if candidate_tokens:
                candidate = _normalise_detected_recipe_line(" ".join(candidate_tokens))
                if candidate and _looks_like_recipe_title_candidate(candidate):
                    return candidate[:_RECIPE_TITLE_MAX_CHARS]

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
    """
    INTERNAL/ADMIN: Upload a PDF for curated cookbook ingestion.
    
    Returns job_id immediately. Background processing via Celery.
    This endpoint is not exposed in the active product UI (M015 pivot).
    Future use: admin-only curated library management.
    """
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
    result = ingest_cookbook.delay(str(job.job_id), str(current_user.user_id), content_b64, file.filename)
    job.celery_task_id = result.id
    db.add(job)
    await db.commit()

    return {"job_id": str(job.job_id)}





@router.get("/{job_id}")
async def get_ingestion_status(job_id: uuid.UUID, db: DBSession, current_user: CurrentUser):
    """
    INTERNAL/ADMIN: Poll ingestion job status.
    
    This endpoint is not exposed in the active product UI (M015 pivot).
    Future use: admin monitoring of curated cookbook uploads.
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
    
    This endpoint is not exposed in the active product UI (M015 pivot).
    Future use: admin control over curated cookbook uploads.
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
            pass

    job.status = IngestionStatus.FAILED
    job.failed = max(job.failed, 1)
    job.completed_at = job.completed_at or datetime.now(timezone.utc).replace(tzinfo=None)
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
