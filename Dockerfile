# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Install Python dependencies into /install prefix so they can be
# copied cleanly to the runtime stage without build tools.
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-prod.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements-prod.txt


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
# Lean runtime image with Tesseract OCR system binary installed.
# pytesseract (in requirements-prod.txt) is a thin wrapper that shells out
# to this binary — both must be present for OCR to work on Linux.
FROM python:3.12-slim

# System packages:
#   tesseract-ocr     — OCR binary that pytesseract calls at runtime
#   tesseract-ocr-eng — English language data (English-only keeps image smaller)
#   libpq5            — PostgreSQL client library needed by asyncpg/psycopg
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . /app
WORKDIR /app

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# API server (default)
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
# Celery worker: docker run <image> celery -A app.workers.celery_app worker --concurrency=1 --pool=solo
