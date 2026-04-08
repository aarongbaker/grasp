# Technology Stack

**Analysis Date:** 2026-04-08

## Languages

**Primary:**
- Python 3.12 - Backend API, LangGraph state machine, task workers, ingestion pipelines
- TypeScript 5.9.3 - React frontend with type safety

**Secondary:**
- SQL (PostgreSQL) - Schema via SQLAlchemy/SQLModel with Alembic migrations
- JavaScript - Frontend build tooling

## Runtime

**Environment:**
- Python 3.12 (specified in `.python-version`)
- Node.js (implied by frontend build scripts)

**Package Manager:**
- pip / PyPI - Python dependencies (`requirements.txt`)
- npm - JavaScript dependencies (`frontend/package.json`)
- Lockfiles present and checked in

## Frameworks

**Backend:**
- FastAPI 0.111.0 - Async web framework with dependency injection
- uvicorn 0.30.1 - ASGI server for FastAPI

**LLM / State Machine:**
- LangGraph 1.0.10 - DAG-based state machine for multi-step meal planning pipeline
- langchain 1.2.10 - LLM orchestration abstractions
- langchain-anthropic 1.3.4 - Claude API integration
- langchain-openai 1.1.10 - OpenAI API integration (text-embedding-3-small for RAG)

**Frontend:**
- React 19.2.4 - Component-based UI
- React Router 7.13.1 - Client-side routing
- Vite 8.0.0 - Build tool and dev server
- TypeScript - Type-safe frontend code

**Testing:**
- pytest 8.2.2 - Python test runner
- pytest-asyncio 0.23.7 - Async test support for FastAPI/LangGraph
- pytest-mock 3.14.0 - Mocking utilities
- httpx 0.27.0 - Async HTTP client for testing FastAPI routes
- vitest 3.2.4 - Frontend test runner (TypeScript/React)
- @testing-library/react 16.3.0 - React component testing

**Build/Dev:**
- ESLint 9.39.4 - Linting for TypeScript/JSX
- TypeScript ESLint 8.56.1 - TS-aware linting rules

## Key Dependencies

**ORM / Database:**
- SQLModel 0.0.19 - SQLAlchemy + Pydantic hybrid ORM for type-safe schema modeling
- asyncpg 0.29.0 - Async PostgreSQL driver (used by FastAPI routes)
- psycopg 3.3.3 (psycopg3) - Sync PostgreSQL driver (used by LangGraph PostgresSaver)
- psycopg-pool 3.2.2 - Connection pooling for LangGraph checkpoint persistence
- Alembic 1.15.2 - Database schema migrations

**Validation / Configuration:**
- Pydantic 2.7.4 - Data validation and settings management
- pydantic-settings 2.3.1 - Environment-based configuration
- email-validator 2.2.0 - Email field validation

**Vector Store / RAG:**
- Pinecone 4.1.0 - Vector database for recipe chunk embeddings (per-user isolation)
- OpenAI embedding client (bundled in langchain-openai) - text-embedding-3-small (1536 dims)

**Scheduling / Task Queue:**
- Celery 5.4.0 - Async task queue for long-running ingestion and LLM jobs
- Redis 5.0.7 - Message broker and result backend for Celery
- kombu 5.3.7 - Celery transport abstraction layer

**Graph / Scheduling:**
- NetworkX 3.3 - DAG construction and cycle detection for recipe scheduling
- LangGraph checkpoint (PostgreSQL + in-memory fallback) - State persistence across graph runs

**Ingredient Parsing:**
- ingredient-parser-nlp 2.6.0 - NLP-based ingredient parsing with Pint unit conversion

**PDF Processing & OCR:**
- pymupdf (fitz) 1.24.5 - PDF rasterization at 300 DPI for ingredient lists
- Pillow 10.3.0 - Image format support for OCR pipeline
- pytesseract - Tesseract OCR binary wrapper (cross-platform fallback)
- Tesseract OCR - System binary (installed in Docker image)
- pyobjc-framework-Vision 11.0 (macOS only) - Apple Vision OCR for cookbook typography
- pyobjc-framework-Quartz 11.0 (macOS only) - CGImage support for Vision pipeline

**Auth / Security:**
- PyJWT 2.9.0 - JWT token encoding/decoding (HS256 access + refresh tokens)
- bcrypt 5.0.0 - Password hashing for admin accounts
- slowapi 0.1.9 - Per-route rate limiting (Redis-backed or in-memory fallback)

**Observability / Resilience:**
- structlog 24.4.0 - Structured logging (JSON in prod, pretty-printed in dev)
- tenacity 9.0.0 - Exponential backoff retry decorator for transient LLM errors

**Utilities:**
- python-dotenv 1.0.1 - Load `.env` files
- python-multipart 0.0.9 - FastAPI multipart form parsing for file uploads

**Admin/Dev UI (Optional):**
- Streamlit 1.55.0 - Interactive Python UI for testing (not required for production)

**PDF Export:**
- @react-pdf/renderer 4.3.2 - Generate PDF exports of meal plans
- framer-motion 12.38.0 - Animation library for UI transitions

**Frontend Icons:**
- lucide-react 0.577.0 - Icon components

## Configuration

**Environment:**
- `.env` file (git-ignored) with required API keys and connection strings
- `.env.example` documents all settings with defaults
- Pydantic `BaseSettings` loads and validates all environment variables at startup

**Build:**
- `tsconfig.json` + `tsconfig.app.json` + `tsconfig.node.json` - TypeScript configuration
- `Dockerfile` - Multi-stage Docker build (builder + runtime stages)
- `alembic.ini` - Database migration configuration
- `vite.config.ts` (implied by `npm run build`) - Frontend build configuration
- `.eslintrc` (implied by `npm run lint`) - Linting configuration

## Platform Requirements

**Development:**
- Python 3.12
- PostgreSQL 16 (local or Docker)
- Redis 7 (local or Docker)
- Node.js + npm (for frontend)
- Tesseract OCR (optional, fallback to pymupdf text extraction)
- macOS for Apple Vision OCR (optional)

**Production:**
- Python 3.12 runtime
- PostgreSQL 16 (managed service or containerized)
- Redis (message broker for Celery)
- Anthropic API key (Claude LLM)
- OpenAI API key (embeddings model)
- Pinecone account + API key (vector database)
- Tesseract OCR binary (Linux Docker image)
- Docker container orchestration (Railway, Fly.io, etc.)

**Deployment:**
- Docker containers (API + Celery worker as separate services)
- Cloudflare Pages (frontend static hosting)
- Environment variables for all secrets (no .env files in production)

---

*Stack analysis: 2026-04-08*
