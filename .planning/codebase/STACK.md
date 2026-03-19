# Technology Stack

**Analysis Date:** 2026-03-18

## Languages

**Primary:**
- Python 3.14.3 - Backend API, LLM orchestration, graph scheduling, ingestion pipelines
- TypeScript 5.9.3 - React frontend with strict type checking
- JavaScript - Node.js tooling and Vite build system

**Secondary:**
- SQL - PostgreSQL schema and migrations (via Alembic)

## Runtime

**Environment:**
- Python 3.14.3 (configured in project)
- Node.js v25.8.0 (frontend dev/build)

**Package Manager:**
- Python: pip (via requirements.txt)
- Node.js: npm
- Lockfile: `package-lock.json` (frontend), `requirements.txt` (pinned Python)

## Frameworks

**Core:**
- FastAPI 0.111.0 - REST API server, async request handling, automatic OpenAPI docs
- React 19.2.4 - Component-based UI with Hooks API
- Vite 8.0.0 - Lightning-fast dev server and production build for frontend

**Graph Orchestration:**
- LangGraph 1.0.10 - State machine DAG for dinner planning pipeline
- LangGraph-Checkpoint-Postgres 3.0.4 - Persistent graph state to PostgreSQL
- LangChain 1.2.10 - LLM abstraction layer

**Testing:**
- pytest 8.2.2 - Python test runner
- pytest-asyncio 0.23.7 - Async test support
- pytest-mock 3.14.0 - Mocking utilities
- httpx 0.27.0 - Async HTTP client for testing FastAPI endpoints

**Build/Dev:**
- TypeScript 5.9.3 - Type checking for frontend
- ESLint 9.39.4 - JavaScript/TypeScript linting
- typescript-eslint 8.56.1 - ESLint plugin for TypeScript
- Vite 8.0.0 - Module bundler and dev server

## Key Dependencies

**Critical:**
- sqlmodel 0.0.19 - SQLAlchemy + Pydantic ORM for type-safe database models
- asyncpg 0.29.0 - High-performance async PostgreSQL driver for FastAPI routes
- psycopg[binary] 3.3.3 - Required by LangGraph PostgresSaver (synchronous driver)
- psycopg-pool 3.2.2 - Connection pooling for LangGraph checkpoint persistence
- alembic 1.15.2 - Database migration management

**LLM Providers:**
- anthropic 0.84.0 - Anthropic API client (primary LLM)
- openai 2.26.0 - OpenAI API client (secondary, for enrichment vision)
- langchain-anthropic 1.3.4 - LangChain wrapper for Claude
- langchain-openai 1.1.10 - LangChain wrapper for GPT

**Vector Store & Search:**
- pinecone 4.1.0 - Pinecone vector database client (v4 API) for recipe RAG retrieval

**Task Queue & Caching:**
- celery 5.4.0 - Distributed task queue for async ingestion jobs
- redis 5.0.7 - Redis client for Celery broker and caching
- kombu 5.3.7 - Message transport layer for Celery (pinned to avoid compatibility breaks)

**Scheduling & Graphs:**
- networkx 3.3 - DAG construction and cycle detection for schedule optimization

**PDF & Image Processing:**
- pymupdf 1.24.5 (fitz) - PDF rasterization to image sequences (300 DPI)
- Pillow 10.3.0 - Image processing and format handling
- pyobjc-framework-Vision 11.0 - macOS-only Apple Vision OCR framework
- pyobjc-framework-Quartz 11.0 - macOS-only CGImage support for Vision pipeline

**Frontend UI:**
- react-router-dom 7.13.1 - Client-side routing
- framer-motion 12.38.0 - Animation library for React
- lucide-react 0.577.0 - Icon library
- @react-pdf/renderer 4.3.2 - PDF generation from React components (for recipe export)

**Authentication & Security:**
- PyJWT 2.9.0 - JWT token encoding/decoding
- bcrypt 5.0.0 - Password hashing with bcrypt
- slowapi 0.1.9 - Per-route rate limiting middleware for FastAPI

**Validation & Configuration:**
- pydantic 2.7.4 - Data validation and settings management
- pydantic-settings 2.3.1 - Environment variable configuration from `.env`

**Observability:**
- structlog 24.4.0 - Structured JSON logging in production, pretty-print in dev
- tenacity 9.0.0 - Retry logic with exponential backoff for transient LLM errors

**Utilities:**
- python-dotenv 1.0.1 - Load `.env` configuration files
- python-multipart 0.0.9 - MultipartForm parser for file uploads in FastAPI
- uvicorn[standard] 0.30.1 - ASGI server (async HTTP server for FastAPI)

**Development UI:**
- streamlit 1.55.0 - Lightweight local dev UI (not required for production)

## Configuration

**Environment:**
- Configured via `.env` file using Pydantic BaseSettings
- `core/settings.py` reads all environment variables at startup
- Config cached with `@lru_cache` to avoid repeated file reads

**Key configs required:**
- `DATABASE_URL` - asyncpg PostgreSQL connection string (FastAPI routes)
- `LANGGRAPH_CHECKPOINT_URL` - psycopg PostgreSQL connection string (LangGraph)
- `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` - Redis/Celery
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` - LLM provider credentials
- `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` - Vector store configuration
- `JWT_SECRET_KEY` - JWT token signing secret
- `APP_ENV` - Environment (development/production)

**Build:**
- Frontend: `vite.config.ts` defines Vite server proxy (`/api` → localhost:8000)
- Backend: `alembic.ini` configures database migrations
- Backend: `uvicorn` ASGI server runs on port 8000

## Platform Requirements

**Development:**
- Python 3.14.3
- Node.js v25.8.0
- Docker (for PostgreSQL and Redis via `docker-compose.yml`)
- PostgreSQL 16-alpine (development instance)
- PostgreSQL 16-alpine (test instance on separate port)
- Redis 7-alpine

**Production:**
- PostgreSQL 16+ with async driver support
- Redis 7+ for Celery broker/backend
- Python 3.11+ runtime environment
- Node.js 18+ for building frontend assets (build-time only)

---

*Stack analysis: 2026-03-18*
