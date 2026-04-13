# Railway Deploy Checklist

Use this when promoting GRASP to production with **Railway for the API + worker** and **Cloudflare Pages for the frontend**.

The checked-in app expects a three-surface deployment contract:

1. **api** service on Railway — FastAPI / `/api/v1`
2. **worker** service on Railway — Celery background execution for session planning
3. **frontend** on Cloudflare Pages — Vite build that calls the Railway API over HTTPS

If you leave `VITE_API_URL` unset, the frontend falls back to same-origin `/api/v1`. That is only correct when the frontend and API are served from the same origin. For the normal Cloudflare Pages → Railway split, you must set `VITE_API_URL` in Cloudflare Pages at build time.

## Services

Create these production services across Railway and Cloudflare:

### Railway

1. **api** — FastAPI service from this repo
2. **worker** — Celery worker from this repo
3. **postgres** — Railway Postgres
4. **redis** — Railway Redis

### Cloudflare Pages

5. **frontend** — Vite/React app from `frontend/`

## Start Commands

### Railway API

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
```

### Railway Worker

```bash
celery -A app.workers.celery_app worker --pool=solo --concurrency=1 --loglevel=INFO
```

The checked-in worker config now sets `broker_connection_retry_on_startup=True` explicitly. That removes the Celery 5.4 pending-deprecation warning about startup retry behavior while preserving the current operator contract: broker reconnect on startup is allowed, but failed tasks are **not** auto-retried.

### Cloudflare Pages build

```bash
cd frontend && npm ci && npm run build
```

Cloudflare Pages output directory:

```text
frontend/dist
```

## Required Environment Variables

Set these on **both** Railway services (`api` and `worker`) unless noted otherwise.

### App (Railway api + worker)

```env
APP_ENV=production
LOG_LEVEL=INFO
```

### Auth (Railway api + worker)

```env
JWT_SECRET_KEY=<generate-a-strong-random-secret>
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60
JWT_REFRESH_EXPIRE_DAYS=7
```

The checked-in API startup code rejects the default placeholder JWT secret when `APP_ENV=production`.

Generate a secret locally with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### CORS (Railway api only)

Must be a JSON array string:

```env
CORS_ALLOWED_ORIGINS=["https://your-frontend-domain.com"]
```

The checked-in API startup code also rejects the localhost dev-origin default when `APP_ENV=production`.

### Postgres (Railway api + worker)

Railway usually gives one connection string. Derive both forms because the app uses two different drivers against the same Postgres instance:

```env
DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db
LANGGRAPH_CHECKPOINT_URL=postgresql://user:pass@host:port/db
```

### Redis / Celery (Railway api + worker)

```env
REDIS_URL=redis://default:password@host:port/0
CELERY_BROKER_URL=redis://default:password@host:port/0
CELERY_RESULT_BACKEND=redis://default:password@host:port/1
```

### Providers (Railway api + worker)

```env
ANTHROPIC_API_KEY=...
```

The API can boot without provider keys, but session planning flows will fail until `ANTHROPIC_API_KEY` is set.

### Frontend build env (Cloudflare Pages only)

```env
VITE_API_URL=https://<railway-api-url>
```

Rules:
- set the API origin only
- do **not** add `/api/v1`
- do **not** leave a trailing slash
- Vite reads this at build time, not runtime

## Preflight Checks

Before deploy, confirm:

- [ ] `.venv/bin/python -m pytest tests/ -m "not integration" -q` passes
- [ ] `npm --prefix frontend run build` passes if deploying the frontend
- [ ] `npm --prefix frontend run lint` passes if deploying the frontend
- [ ] JWT secret is not the default value
- [ ] `CORS_ALLOWED_ORIGINS` is a JSON array string containing only real frontend origin(s), not localhost defaults
- [ ] Railway API env includes both `DATABASE_URL` and `LANGGRAPH_CHECKPOINT_URL` with the correct schemes
- [ ] Railway API env includes `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND`
- [ ] Worker uses `--pool=solo --concurrency=1`
- [ ] Cloudflare Pages build env sets `VITE_API_URL=https://<railway-api-url>` if frontend and API are on different origins
- [ ] Alembic migrations have been run in a deploy/pre-deploy step (`alembic upgrade head`)

## Smoke Test After Deploy

Run these against the live stack.

### Railway API

1. `GET /api/v1/health` returns 200
2. Register a user
3. Request `/api/v1/auth/token`
4. Create a session
5. `POST /api/v1/sessions/{id}/run`
6. Confirm the worker picks up the task
7. Poll `/api/v1/sessions/{id}` until terminal
8. Fetch `/api/v1/sessions/{id}/results`

### Cloudflare Pages frontend

9. Load the Pages site in the browser
10. Confirm login/register screens can reach the API without CORS failures
11. Confirm the app is talking to the intended Railway host (not same-origin `/api/v1` on the Pages domain)

## Known Constraints

- The worker process must still be pinned to `--pool=solo --concurrency=1` for current Railway memory assumptions, even though the checked-in Celery config makes broker startup retry explicit.
- Provider keys are not enforced at API boot. Missing `ANTHROPIC_API_KEY` shows up when planning flows execute.
- Celery may still emit a warning when the worker runs as root inside a container. Treat that as container-user hygiene, not as a reachability or broker-startup blocker.
- Frontend deployment is separate and must be validated with the Cloudflare build-time `VITE_API_URL` value, not only with API health.
