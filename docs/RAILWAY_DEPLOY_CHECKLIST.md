# Railway Deploy Checklist

Use this when promoting the backend to Railway.

## Services

Create four services in one Railway project:

1. **api** — FastAPI service from this repo
2. **worker** — Celery worker from this repo
3. **postgres** — Railway Postgres
4. **redis** — Railway Redis

## Start Commands

### API

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
```

### Worker

```bash
celery -A app.workers.celery_app worker --pool=solo --concurrency=1 --loglevel=INFO
```

## Required Environment Variables

Set these on **both** api and worker unless noted otherwise.

### App

```env
APP_ENV=production
LOG_LEVEL=INFO
```

### Auth

```env
JWT_SECRET_KEY=<generate-a-strong-random-secret>
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60
JWT_REFRESH_EXPIRE_DAYS=7
```

Generate a secret locally with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### CORS (API only)

Must be a JSON array string:

```env
CORS_ALLOWED_ORIGINS=["https://your-frontend-domain.com"]
```

### Postgres

Railway usually gives one connection string. Derive both forms:

```env
DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db
LANGGRAPH_CHECKPOINT_URL=postgresql://user:pass@host:port/db
```

### Redis / Celery

```env
REDIS_URL=redis://default:password@host:port/0
CELERY_BROKER_URL=redis://default:password@host:port/0
CELERY_RESULT_BACKEND=redis://default:password@host:port/1
```

### Providers

```env
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=grasp-cookbooks
PINECONE_ENVIRONMENT=us-east-1-aws
```

## Preflight Checks

Before deploy, confirm:

- [ ] `pytest tests/ -m "not integration" -q` passes
- [ ] `ruff check app/ tests/` passes
- [ ] Pinecone index exists
- [ ] JWT secret is not the default value
- [ ] CORS origin is not localhost
- [ ] Worker uses `--pool=solo --concurrency=1`

## Smoke Test After Deploy

Run these against the live API.

1. `GET /api/v1/health` returns 200
2. Register a user
3. Request `/api/v1/auth/token`
4. Create a session
5. `POST /api/v1/sessions/{id}/run`
6. Confirm the worker picks up the task
7. Poll `/api/v1/sessions/{id}` until terminal
8. Fetch `/api/v1/sessions/{id}/results`

## Known Constraints

- PDF ingestion currently sends PDF bytes through the API into Celery. This works for staging and small usage, but object storage is still the right production follow-up.
- Frontend deployment is separate and should be validated independently.
