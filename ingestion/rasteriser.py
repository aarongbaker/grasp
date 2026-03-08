"""
ingestion/rasteriser.py
Phase 2a: PDF → 300 DPI images → Apple Vision OCR → PageCache.

Mac-only in V1. Apple Vision is accessed via PyObjC (Vision framework).
Tesseract is the V2 cross-platform fallback — deferred by design.

The conditional import pattern: if not on macOS or PyObjC not available,
falls back to pymupdf text extraction. This allows the prototype to run
on non-Mac machines with reduced OCR quality (acceptable for development).

300 DPI is the minimum safe resolution for stylised cookbook fonts and
dense ingredient lists. No quality branching — always rasterise at 300 DPI.

PageCache is written BEFORE any further processing. If the classifier or
state machine crashes, the OCR output is safe in Postgres. Reprocessing
is free — re-running OCR is not.
"""

import sys
import hashlib
import asyncio
from pathlib import Path
from typing import Optional
import fitz  # pymupdf


def _ocr_page_apple_vision(image_bytes: bytes) -> tuple[str, float]:
    """
    Apple Vision OCR. Mac-only. Returns (text, confidence).
    Layout-aware — handles two-column cookbook typography, stylised fonts,
    photography captions that pdfplumber would mangle.
    """
    try:
        import objc
        from Vision import VNRecognizeTextRequest, VNImageRequestHandler
        import Quartz

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
    pdf_bytes: bytes,
    book_id: str,
    user_id: str,
    db,  # AsyncSession
) -> list[dict]:
    """
    Rasterises every page at 300 DPI, runs OCR, writes PageCache rows.
    Returns list of page dicts (page_number, text, confidence, page_hash).
    """
    from models.ingestion import PageCache
    import uuid

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    is_mac = sys.platform == "darwin"

    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(300 / 72, 300 / 72)  # 300 DPI
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")

        # SHA256 of original PDF page bytes (not the image — stable across re-runs)
        page_content = page.get_text("rawdict")
        page_hash = hashlib.sha256(str(page_content).encode()).hexdigest()

        if is_mac:
            text, confidence = await asyncio.to_thread(_ocr_page_apple_vision, img_bytes)
            if not text:
                text, confidence = _ocr_page_pymupdf_fallback(page)
        else:
            text, confidence = _ocr_page_pymupdf_fallback(page)

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
        pages.append({
            "page_number": page_num + 1,
            "text": text,
            "confidence": confidence,
            "page_hash": page_hash,
        })

    await db.commit()
    doc.close()
    return pages
