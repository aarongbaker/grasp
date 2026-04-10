"""
ingestion/rasteriser.py

INTERNAL INFRASTRUCTURE ONLY — Phase 2a: PDF → 300 DPI images → OCR → PageCache.

After M015 pivot (cookbook de-scope), this module is used only for team/admin
curated cookbook uploads, not user-facing upload flows.

OCR backend selection (priority order):
  macOS: Apple Vision (PyObjC) → Tesseract → pymupdf text extraction
  Linux: Tesseract → pymupdf text extraction

Why three backends?
  - Apple Vision provides best quality for stylised cookbook fonts (serif
    display typefaces, hand-lettered headers, dense two-column layouts).
    It's layout-aware and handles text that pdfplumber/pymupdf would mangle.
  - Tesseract is the cross-platform production backend for Docker/Linux
    (Railway). Requires the tesseract-ocr system package. Good quality on
    clean scans, lower quality on stylised fonts than Vision.
  - pymupdf text extraction is a last-resort fallback — it reads embedded
    PDF text streams, not rendered page images. Works on digitally-created
    PDFs but gives poor output for scanned cookbooks.

300 DPI: the minimum safe resolution for stylised cookbook fonts and dense
  ingredient lists. Lower DPI (150, 200) produces OCR errors on serif display
  fonts commonly used in cookbook headings. Always rasterise at 300 — no
  quality branching.

PageCache write-before-processing:
  OCR output is written to Postgres before classifier/state machine processing.
  If the classifier or state machine crashes, the OCR output is safe and
  re-processing only needs to re-run the downstream phases (free).
  Re-running OCR (~$0.001/page in compute cost) is wasteful and slow.

Memory management:
  300 DPI PNG images of cookbook pages are 10-20 MB each. The pixmap and
  image bytes are freed immediately after OCR completes — keeping all pages
  in memory simultaneously would exhaust available RAM for large cookbooks.

See: .gsd/milestones/M015/slices/S03/S03-CONTEXT.md for enrichment contract.
"""

import asyncio
import hashlib
import logging
import sys
from pathlib import Path
from typing import Optional

import fitz  # pymupdf

logger = logging.getLogger(__name__)

# Detect available OCR backends at import time — done once so we don't attempt
# pytesseract.get_tesseract_version() on every page (expensive syscall).
# _HAS_TESSERACT is used in rasterise_and_ocr_pdf() to select the backend path.
_HAS_TESSERACT = False
try:
    import pytesseract
    pytesseract.get_tesseract_version()
    _HAS_TESSERACT = True
except Exception:
    pass


def _ocr_page_apple_vision(image_bytes: bytes) -> tuple[str, float]:
    """
    Apple Vision OCR. Mac-only. Returns (text, confidence).

    Layout-aware — handles two-column cookbook typography, stylised serif
    display fonts, and photography captions that pdfplumber/pymupdf would
    mangle or omit entirely.

    Implementation notes:
      - NSData wraps the PNG bytes for the Quartz framework
      - CGImageSourceCreateWithData + CGImageSourceCreateImageAtIndex converts
        NSData to a CGImage suitable for the Vision framework
      - VNRecognizeTextRequest with VNRequestTextRecognitionLevelAccurate (1)
        uses the neural network OCR path (vs fast mode = heuristic OCR)
      - setUsesLanguageCorrection_(True) runs a language model pass to correct
        common OCR errors (e.g. "I" vs "l" ambiguity in ingredient lists)
      - Confidence is per-observation (text region), averaged across the page.
        Values typically 0.85-0.99 for clean cookbook scans.

    Returns ("", 0.0) on any failure — the caller falls back to Tesseract.
    Failure is expected on non-Mac systems where PyObjC is not installed.
    """
    try:
        import objc
        import Quartz
        from Vision import VNImageRequestHandler, VNRecognizeTextRequest

        # Convert bytes → CGImage via NSData + Quartz image source
        data = objc.lookUpClass("NSData").dataWithBytes_length_(image_bytes, len(image_bytes))
        image_source = Quartz.CGImageSourceCreateWithData(data, None)
        cg_image = Quartz.CGImageSourceCreateImageAtIndex(image_source, 0, None)

        results = []
        confidences = []

        def handler(request, error):
            for obs in request.results():
                results.append(obs.topCandidates_(1)[0].string())
                confidences.append(obs.topCandidates_(1)[0].confidence())

        request = VNRecognizeTextRequest.alloc().initWithCompletionHandler_(handler)
        request.setRecognitionLevel_(1)  # VNRequestTextRecognitionLevelAccurate
        request.setUsesLanguageCorrection_(True)

        handler_obj = VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, {})
        handler_obj.performRequests_error_([request], None)

        text = "\n".join(results)
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return text, avg_confidence

    except Exception as e:
        logger.warning("Apple Vision OCR failed: %s", e)
        return "", 0.0


def _ocr_page_tesseract(image_bytes: bytes) -> tuple[str, float]:
    """
    Tesseract OCR. Linux production backend. Returns (text, confidence).

    Uses pytesseract wrapper around the tesseract-ocr CLI. Requires the
    tesseract-ocr system package to be installed (included in the Railway
    Dockerfile via apt-get).

    lang="eng": English-only language model. Cookbook content is English.
    Using multiple language models increases memory and latency with no
    benefit for our corpus.

    Confidence is synthetic (0.85) — pytesseract.image_to_string() does not
    expose per-page confidence in a simple API call. The value 0.85 was chosen
    to reflect Tesseract's typical accuracy on clean cookbook scans:
      - Above pymupdf's 0.7 (pymupdf is a lower-quality extraction method)
      - Below Apple Vision's ~0.95 (Vision is higher quality on stylised fonts)

    Returns ("", 0.0) on failure — the caller falls back to pymupdf.
    """
    try:
        import io

        import pytesseract
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(image, lang="eng")
        return text.strip(), 0.85
    except Exception as e:
        logger.warning("Tesseract OCR failed: %s", e)
        return "", 0.0


def _ocr_page_pymupdf_fallback(page) -> tuple[str, float]:
    """
    pymupdf text extraction fallback. Returns (text, confidence).

    Reads embedded text streams from the PDF — not OCR. Works well for
    digitally-created PDFs (e.g. exported from InDesign) but poorly for
    scanned historical cookbooks where text is embedded as image data.

    Confidence is synthetic (0.7): the lowest of the three backends,
    reflecting that pymupdf extraction:
      - Gives no quality signal of its own
      - Fails silently on image-based PDFs (returns empty string)
      - Has known issues with two-column layouts (merges columns incorrectly)

    This is called as a last resort. If it returns empty text, the page
    is stored as empty in PageCache — the state machine will skip it.
    """
    text = page.get_text("text")
    return text, 0.7


async def rasterise_and_ocr_pdf(
    pdf_source: bytes | str | Path,
    book_id: str,
    user_id: str,
    db,  # AsyncSession
    progress_callback=None,
) -> list[dict]:
    """
    Rasterises every page at 300 DPI, runs OCR, writes PageCache rows.
    Returns list of page dicts (page_number, text, confidence, page_hash).

    pdf_source: file path (str/Path) preferred — pymupdf memory-maps it,
      avoiding loading the entire PDF into RAM. bytes still accepted for
      backwards compatibility with the API route that reads the upload into
      memory before enqueueing (V1 limitation — V2 should pass an object
      storage URL instead).

    Page hash (SHA256):
      Computed from the pymupdf embedded text, not from the OCR output.
      pymupdf text extraction is deterministic and fast — it's used only
      for hashing, not as the final text. OCR output varies slightly across
      runs due to language model stochasticity; the embedded text is stable.
      This makes the hash a reliable fingerprint for deduplication.

    Backend selection per page:
      macOS: Vision → Tesseract (if Vision fails) → pymupdf (if Tesseract fails)
      Linux: Tesseract → pymupdf (if Tesseract fails)
      Both use asyncio.to_thread() for the blocking OCR call to avoid blocking
      the event loop during the ~100-500ms per-page OCR operation.

    Memory management:
      - pix (pixmap): freed immediately after tobytes("png") — 10-20 MB per page
      - img_bytes: freed after OCR — no longer needed once text is extracted
      - _COMMIT_BATCH: flush PageCache rows to DB every 50 pages to bound
        session memory. Without this, a 200-page cookbook accumulates 200
        ORM objects in the session before the final commit.

    progress_callback: optional async callable(page_num, total_pages).
      Called after each page so the Celery task can update the IngestionJob
      status with OCR progress (displayed in the admin monitoring UI).
    """
    import uuid

    from app.models.ingestion import PageCache

    if isinstance(pdf_source, (str, Path)):
        doc = fitz.open(str(pdf_source))
    else:
        # bytes path: fitz.open(stream=...) loads into memory — not ideal for large PDFs
        doc = fitz.open(stream=pdf_source, filetype="pdf")
    pages = []
    is_mac = sys.platform == "darwin"
    _COMMIT_BATCH = 50  # flush to DB every N pages to limit session memory

    for page_num in range(len(doc)):
        page = doc[page_num]

        # Rasterise at 300 DPI — matrix scales from PDF's 72 DPI default
        mat = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")

        # Free the pixmap immediately — 300 DPI images are 10-20 MB each.
        # Keeping the pixmap alive until end-of-loop would double memory use.
        del pix

        # Page hash: derived from embedded text (fast, deterministic) not OCR output.
        # Used for deduplication and change detection on re-ingestion.
        page_text_for_hash = page.get_text("text")
        page_hash = hashlib.sha256(page_text_for_hash.encode()).hexdigest()

        # OCR with backend priority — asyncio.to_thread() for non-blocking execution
        if is_mac:
            text, confidence = await asyncio.to_thread(_ocr_page_apple_vision, img_bytes)
            if not text and _HAS_TESSERACT:
                text, confidence = await asyncio.to_thread(_ocr_page_tesseract, img_bytes)
            if not text:
                text, confidence = _ocr_page_pymupdf_fallback(page)
        elif _HAS_TESSERACT:
            text, confidence = await asyncio.to_thread(_ocr_page_tesseract, img_bytes)
            if not text:
                text, confidence = _ocr_page_pymupdf_fallback(page)
        else:
            text, confidence = _ocr_page_pymupdf_fallback(page)

        # Free image bytes after OCR — no longer needed
        del img_bytes

        # Write PageCache row immediately (before classifier/state machine).
        # If downstream processing fails, OCR output is safe in Postgres.
        cache_row = PageCache(
            page_id=uuid.uuid4(),
            book_id=uuid.UUID(book_id),
            page_number=page_num + 1,  # 1-indexed for human readability
            page_text=text,
            page_hash=page_hash,
            vision_confidence=confidence,
            resolution_dpi=300,
        )
        db.add(cache_row)
        pages.append(
            {
                "page_number": page_num + 1,
                "text": text,
                "confidence": confidence,
                "page_hash": page_hash,
            }
        )

        if progress_callback is not None:
            await progress_callback(page_num + 1, len(doc))

        # Batch-commit to avoid holding all PageCache objects in session memory.
        # After 50 pages, flush to DB and clear the session's identity map.
        if (page_num + 1) % _COMMIT_BATCH == 0:
            await db.commit()

    # Final commit for the last partial batch
    await db.commit()
    doc.close()
    return pages
