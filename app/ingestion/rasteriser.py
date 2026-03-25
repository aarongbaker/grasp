"""
ingestion/rasteriser.py
Phase 2a: PDF → 300 DPI images → OCR → PageCache.

OCR backend selection (priority order):
  macOS: Apple Vision (PyObjC) -> Tesseract -> pymupdf text extraction
  Linux: Tesseract -> pymupdf text extraction

Apple Vision provides best quality for stylised cookbook fonts.
Tesseract is the cross-platform production backend (Docker/Linux).
pymupdf text extraction is the last-resort fallback on any platform.

300 DPI is the minimum safe resolution for stylised cookbook fonts and
dense ingredient lists. No quality branching — always rasterise at 300 DPI.

PageCache is written BEFORE any further processing. If the classifier or
state machine crashes, the OCR output is safe in Postgres. Reprocessing
is free — re-running OCR is not.
"""

import asyncio
import hashlib
import logging
import sys
from pathlib import Path
from typing import Optional

import fitz  # pymupdf

logger = logging.getLogger(__name__)

# Detect available OCR backends at import time
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
    Layout-aware — handles two-column cookbook typography, stylised fonts,
    photography captions that pdfplumber would mangle.
    """
    try:
        import objc
        import Quartz
        from Vision import VNImageRequestHandler, VNRecognizeTextRequest

        # Convert bytes → CGImage
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
    Uses pytesseract wrapper. Requires tesseract-ocr system package.
    Confidence is synthetic (0.85) — Tesseract does not provide per-page
    confidence in a simple API call, and 0.85 reflects its typical accuracy
    on clean cookbook scans (between Vision's ~0.95 and pymupdf's 0.7).
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
    pymupdf text extraction fallback. Used on non-Mac or when Vision unavailable.
    Lower quality than Apple Vision — doesn't handle stylised fonts or columns.
    Confidence is synthetic (0.7 = known fallback).
    """
    text = page.get_text("text")
    return text, 0.7


async def rasterise_and_ocr_pdf(
    pdf_source: bytes | str | Path,
    book_id: str,
    user_id: str,
    db,  # AsyncSession
) -> list[dict]:
    """
    Rasterises every page at 300 DPI, runs OCR, writes PageCache rows.
    Returns list of page dicts (page_number, text, confidence, page_hash).

    pdf_source: file path (str/Path) preferred — pymupdf memory-maps it.
                bytes still accepted for backwards compatibility.
    """
    import uuid

    from app.models.ingestion import PageCache

    if isinstance(pdf_source, (str, Path)):
        doc = fitz.open(str(pdf_source))
    else:
        doc = fitz.open(stream=pdf_source, filetype="pdf")
    pages = []
    is_mac = sys.platform == "darwin"
    _COMMIT_BATCH = 50  # flush to DB every N pages to limit session memory

    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(300 / 72, 300 / 72)  # 300 DPI
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")

        # Free the pixmap immediately — 300 DPI images are 10-20 MB each
        del pix

        # SHA256 of page text content — stable across re-runs, much lighter than rawdict
        page_text_for_hash = page.get_text("text")
        page_hash = hashlib.sha256(page_text_for_hash.encode()).hexdigest()

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

        cache_row = PageCache(
            page_id=uuid.uuid4(),
            book_id=uuid.UUID(book_id),
            page_number=page_num + 1,
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

        # Batch-commit to avoid holding all PageCache objects in session
        if (page_num + 1) % _COMMIT_BATCH == 0:
            await db.commit()

    await db.commit()
    doc.close()
    return pages
