# Domain Pitfalls: Deploying GRASP to Production

**Domain:** FastAPI + React + Celery + PostgreSQL/pgvector — free-tier hosting
**Researched:** 2026-03-19
**Confidence:** HIGH (codebase-verified + well-known deployment failure modes for this stack)

---

## Critical Pitfalls

Mistakes that cause silent data loss, complete deployment failure, or security breaches.

---

### Pitfall 1: JWT Secret Uses Default Value in Production

**What goes wrong:** The codebase already has this guard (`main.py` lines 51-58), but it only fires at startup with `app_env == "production"`. If `APP_ENV` is not set to `"production"` in the hosting environment, the guard is bypassed silently — the default `"change-me-in-production"` secret is used, all JWT tokens are forgeable.

**Why it happens:** Developers set secrets in `.env` locally but forget to configure them as environment variables on the host platform. The guard only runs if `APP_ENV=production` is explicitly set.

**Consequences:** Any user can forge an auth token for any account. Complete authentication bypass.

**Prevention:**
- Harden the guard: raise `RuntimeError` on default JWT secret regardless of `app_env` (or add a second startup check that always warns loudly in logs).
- Add `APP_ENV=production` as a required environment variable in the deploy checklist.
- Generate the secret with: `python -c "import secrets; print(secrets.token_urlsafe(64))"` and store it in the platform's secret manager (not in a `.env` file committed to git).

**Detection:** Check startup logs. The warning `"JWT_SECRET_KEY is using the default value"` appears if the guard fires but does not raise. If the log line is absent entirely, the guard was bypassed.

**Deploy phase:** Must be resolved before first deploy. Block deployment until verified.

---

### Pitfall 2: pgvector Extension Not Available on Free-Tier Postgres

**What goes wrong:** The ingestion pipeline uses Pinecone (not pgvector) for the vector store, so the vector search itself is not blocked. However, LangGraph's `AsyncPostgresSaver` (used for checkpointing) requires a working Postgres connection with the `uuid-ossp` extension. Standard free-tier managed Postgres (e.g., Render free tier, Supabase free tier) may be Postgres 15/16 without `pgvector` installed — but more critically, `langgraph-checkpoint-postgres` runs `CREATE EXTENSION IF NOT EXISTS uuid-ossp` at setup, which requires superuser or `CREATE EXTENSION` privilege. Many managed free-tier Postgres providers restrict this.

**Why it happens:** The `checkpointer.setup()` call in `main.py` (line 93) runs DDL including extension creation. On a managed Postgres with restricted privileges, this raises a `ProgrammingError` and falls back to `MemorySaver` — but the fallback is silent in production (logged as warning, not exception).

**Consequences:** Pipeline checkpoints are not persisted across Celery worker restarts. A worker crash mid-pipeline = silent state loss. Users see sessions stuck in `GENERATING` with no recovery path.

**Prevention:**
- Use **Supabase** (free tier) — it ships Postgres 15 with pgvector pre-installed and grants extension creation in the default schema. Confirmed working with LangGraph checkpointer.
- Or use **Neon** (free tier) — Postgres 16, pgvector available, extensions permitted.
- Verify extension availability before deploying: `psql $DATABASE_URL -c "SELECT * FROM pg_available_extensions WHERE name = 'vector';"`.
- If the fallback to `MemorySaver` fires, log it as ERROR not WARNING so it is not missed.

**Detection:** Startup log line `"LangGraph init failed (%s). Using MemorySaver fallback."` — this is currently logged at WARNING and will be easy to miss.

**Deploy phase:** Hosting platform selection phase. Resolve before any deploy attempt.

---

### Pitfall 3: Alembic Migrations Run Against a Shared Connection Pool at Startup

**What goes wrong:** `main.py` calls `command.upgrade(alembic_cfg, "head")` synchronously inside the `async` lifespan hook (lines 62-67). Alembic uses a synchronous `psycopg2`/`psycopg3` connection internally. On managed Postgres with strict connection limits (Supabase free tier: 25 connections, Neon free tier: 100), this works fine. But the `alembic.ini` has `sqlalchemy.url` hardcoded to localhost (line near end of file) — if Alembic's `env.py` fails to load `core.settings`, the migration runs against localhost and silently does nothing.

**Why it happens:** Alembic's `env.py` reads settings dynamically, but if the import fails (e.g., `ANTHROPIC_API_KEY` is missing and `Settings` construction fails), the `sqlalchemy.url` fallback in `alembic.ini` is used — which points to `localhost:5432`.

**Consequences:** Tables are never created. The app starts, the health check passes (because it runs `SELECT 1` and gets a connection), but every API route that touches user data returns 500.

**Prevention:**
- In `alembic/env.py`, make settings loading failure a hard error, not a fallback.
- Run `alembic upgrade head` as a separate step in the deploy process before starting the app, not inside the lifespan hook. A one-time migration CLI step is safer and gives clear error output.
- Add a post-deploy smoke test: `GET /api/v1/health` is not sufficient — also test `POST /api/v1/users` (exercises user_profiles table).

**Detection:** App starts and health check passes, but `POST /api/v1/sessions` returns `500 relation "sessions" does not exist`.

**Deploy phase:** Deployment pipeline setup. Address in the "first deploy" phase.

---

### Pitfall 4: CORS Misconfiguration — Frontend Gets 403 on All Requests

**What goes wrong:** `core/settings.py` line 26 defaults `cors_allowed_origins` to `["http://localhost:3000", "http://localhost:8501"]`. In production, the Vite frontend is served from a different origin (e.g., `https://grasp.vercel.app`). Unless `CORS_ALLOWED_ORIGINS` is set in the hosting environment as a valid JSON list, every API call from the frontend fails with a CORS error — which browsers report as a generic network error, not a 403.

**Why it happens:** `pydantic-settings` parses list fields from environment variables as JSON. The common mistake is setting `CORS_ALLOWED_ORIGINS=https://grasp.vercel.app` without JSON brackets, which causes a Pydantic validation error at startup, crashing the app before CORS is configured.

**Consequences:** Complete frontend breakage. All API calls fail. Users see a blank screen or "network error".

**Prevention:**
- Set `CORS_ALLOWED_ORIGINS='["https://your-frontend-url.vercel.app"]'` — JSON array with outer single quotes, inner double quotes.
- Add the API URL and allowed origins to the deploy checklist before testing.
- Consider adding a startup log line that prints the resolved CORS origins so they are visible in the deploy log.

**Detection:** Browser console shows `"Access to fetch at 'https://...' from origin '...' has been blocked by CORS policy"`. FastAPI never receives the request.

**Deploy phase:** First deploy configuration. Must be done before any frontend testing.

---

### Pitfall 5: Celery Worker Concurrency = 4 Exhausts Free-Tier Memory

**What goes wrong:** `celery_worker_concurrency = 4` in `core/settings.py` (line 65). Each Celery worker process running the GRASP pipeline loads: LangGraph graph, LangChain + Anthropic client, NetworkX, Pillow (for ingestion), and makes multiple synchronous `asyncio.run()` calls. The LangGraph pipeline alone uses approximately 150–300 MB per concurrent run (LLM clients, model state, checkpoint connection). With 4 concurrent workers in a single process group, peak memory is 600 MB–1.2 GB.

Free tier RAM limits:
- Render free tier: 512 MB total
- Railway hobby: 512 MB
- Fly.io free: 256 MB

**Consequences:** Worker process is OOM-killed mid-pipeline. Task is lost (even though `task_acks_late=True` prevents broker message loss, the worker is dead). Session is left in `GENERATING` status permanently — the `finalise_session()` call never happens.

**Prevention:**
- Set `CELERY_WORKER_CONCURRENCY=1` in the production environment. With 2-5 users and non-realtime requirements, serial execution is fine.
- Use `--pool=solo` in the Celery worker startup command to avoid forking overhead entirely: `celery -A workers.celery_app worker --pool=solo --concurrency=1`
- Consider separating the cookbook ingestion task (which loads Pillow + PyMuPDF) from the pipeline task — they have very different memory profiles.

**Detection:** Session stuck in `GENERATING`. Worker logs show OOM kill signal or `MemoryError`. Platform dashboard shows memory spike.

**Deploy phase:** Worker deployment configuration. Set before first pipeline run.

---

## Moderate Pitfalls

---

### Pitfall 6: Free-Tier Services Sleep — Cold Start Kills Long-Running Requests

**What goes wrong:** Free-tier web services on Render, Railway, and similar platforms spin down after 15 minutes of inactivity. When a user hits the app after inactivity:

1. The API cold-start takes 10–30 seconds.
2. The frontend's 30-second request timeout (`client.ts` line 67) fires first.
3. The user sees "Request timed out — is the server running?" from the ApiError.

More critically: if the Celery worker sleeps between the `POST /sessions/{id}/run` call (which returns 202) and when the worker wakes to process the task, the pipeline task sits in the Redis queue — which is also a free-tier service that may have evicted state if it too slept.

**Prevention:**
- Use a free-tier uptime pinger (UptimeRobot free tier pings every 5 minutes) to prevent sleep.
- Increase the frontend request timeout to 60 seconds for the session run endpoint specifically.
- Use **persistent Redis** (not ephemeral) — Upstash has a free tier with persistence, unlike Render's Redis which uses ephemeral storage.

**Detection:** Users report the UI freezing on first load. Browser network tab shows the first API request taking 10–30 seconds.

**Deploy phase:** Post-deploy hardening. Can be addressed after confirming the basic deploy works.

---

### Pitfall 7: Celery Task Cancellation Does Not Work Across Services

**What goes wrong:** `celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")` in `sessions.py` (line 123) requires the Celery worker to be reachable via the broker's control channel. If the worker is deployed as a separate service (which it must be on most free-tier platforms — you can't run both API and Celery in one dyno reliably), the `revoke` control message is sent correctly. But if the worker has slept or crashed, the `SIGTERM` never arrives, and the session status is set to `CANCELLED` in the DB while the task may later complete and call `finalise_session()` — which checks for `CANCELLED` and correctly skips writing, so the cancel is safe. However the Celery task itself may linger in the queue indefinitely.

**Consequence:** Minor: orphaned tasks in the Redis queue. Not a data integrity issue because `finalise_session` has the cancellation guard (`if result.status == SessionStatus.CANCELLED: return`). But Redis queue can accumulate stale tasks if a user spams cancel/run.

**Prevention:** Add a `task_time_limit` (hard kill) in addition to `task_soft_time_limit` so orphaned tasks do not run indefinitely. Add `celery_task_time_limit = 660` (10% above soft limit) to settings.

**Detection:** Redis queue length growing unboundedly. Check `celery inspect reserved`.

**Deploy phase:** Worker deployment configuration. Low priority for 2-5 users.

---

### Pitfall 8: Secrets Leaked via Environment Variable Injection in Logs

**What goes wrong:** `structlog` is configured for JSON output in production. If an exception is raised that includes a settings object in its traceback (e.g., a connection error to Pinecone that formats the URL with embedded API key), the API key appears in structured logs. FastAPI also logs all incoming headers by default at DEBUG level — including `Authorization: Bearer <token>` if log level is set too low.

**Why it happens:** `LOG_LEVEL=DEBUG` is easy to set during debugging and forget.

**Consequences:** API keys and JWT tokens in log files that may be exported to a third-party log aggregator.

**Prevention:**
- Ensure `LOG_LEVEL=INFO` in production environment (not DEBUG).
- Never construct URLs with embedded credentials — use separate host/user/password settings and connect with keyword args, not connection string interpolation where the key is inline.
- Pinecone client is initialized with `api_key=settings.pinecone_api_key` (not in a URL) — this is already safe.

**Detection:** Grep production logs for `Bearer ` or `api_key`. If found at INFO level, there is a log leakage issue.

**Deploy phase:** Production configuration review. Address before sharing the URL publicly.

---

### Pitfall 9: LangGraph Checkpoint Table Setup Races with Alembic Migrations

**What goes wrong:** `main.py` runs Alembic migrations (step 1) then calls `checkpointer.setup()` (step 3 inside LangGraph init). `checkpointer.setup()` creates its own tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_migrations`) in the same database. If a subsequent Alembic `autogenerate` command is run, it will see these LangGraph tables and try to generate DROP statements for them (if using `include_schemas=True` or not filtering unmanaged tables).

**Why it happens:** LangGraph uses its own DDL separate from Alembic. Alembic's autogenerate does not know these tables are owned by a library.

**Consequences:** An accidental `alembic revision --autogenerate` + `alembic upgrade head` drops the checkpoint tables, deleting all in-progress and historical pipeline states.

**Prevention:**
- Add the LangGraph table names to Alembic's `exclude_tables` list in `alembic/env.py`:
  ```python
  def include_object(object, name, type_, reflected, compare_to):
      if type_ == "table" and name.startswith("checkpoint"):
          return False
      return True
  ```
- Never run `alembic revision --autogenerate` against the production database. Generate migrations locally against a dev database.

**Detection:** Alembic generates a migration with `op.drop_table("checkpoints")` or similar.

**Deploy phase:** Migration setup phase. Add the filter before first production Alembic run.

---

### Pitfall 10: Vite Build Hardcodes the API Base URL

**What goes wrong:** `frontend/src/api/client.ts` line 1 sets `const API_BASE = '/api/v1'` — a relative URL. This works if the frontend and backend are served from the same origin. On a typical free-tier deploy, the frontend is hosted on Vercel/Netlify and the backend on Render/Railway — **different origins**. Relative URLs resolve to the frontend host, not the API host.

**Why it happens:** The relative URL works perfectly during local development (same localhost, different ports proxied by Vite). It silently breaks in production because the frontend now points to `https://grasp.vercel.app/api/v1` — which does not exist.

**Consequences:** Every API call 404s on the frontend CDN. No backend requests reach the API.

**Prevention (two options):**
1. **Same-origin deployment:** Serve the React build as a static directory from FastAPI using `StaticFiles`. This avoids the URL problem entirely and simplifies the deploy to one service. For 2-5 users this is the right choice.
2. **Cross-origin deployment:** Set `VITE_API_BASE_URL` as a Vite env variable and update `client.ts` to use `import.meta.env.VITE_API_BASE_URL || '/api/v1'`.

Option 1 is strongly recommended for this milestone — fewer services, fewer CORS issues, easier to reason about.

**Detection:** Browser network tab shows 404 on all `/api/v1/*` requests, response comes from the CDN not the API.

**Deploy phase:** Architecture decision that must be made before any frontend deploy. Critical.

---

### Pitfall 11: pyobjc Packages Fail to Install on Linux

**What goes wrong:** `requirements.txt` includes:
```
pyobjc-framework-Vision==11.0; sys_platform == "darwin"
pyobjc-framework-Quartz==11.0; sys_platform == "darwin"
```
The `sys_platform == "darwin"` marker correctly excludes these on Linux. However, `pymupdf==1.24.5` (used for PDF rasterisation in the ingestion pipeline) has platform-specific wheels. The `ingest_cookbook` Celery task imports `rasterise_and_ocr_pdf` which uses the macOS Vision framework for OCR — this code path will fail silently on Linux because the Apple Vision API is unavailable.

**Consequences:** Cookbook ingestion fails on any Linux-based host. The task catches the exception and marks `IngestionStatus.FAILED`, so it is not a crash — but the feature is completely non-functional.

**Prevention:**
- Add a Linux-compatible OCR fallback (e.g., `pytesseract`) for the ingestion pipeline before deploying.
- Or disable the cookbook ingestion feature in the initial deploy and document it as macOS-only for now.
- The planning pipeline (generator → enricher → renderer) does NOT use Vision OCR and will work fine on Linux.

**Detection:** Ingest a cookbook PDF. Job shows `FAILED` status immediately. Worker logs show `ModuleNotFoundError: No module named 'Vision'` or similar.

**Deploy phase:** Pre-deploy compatibility check. Must be resolved if cookbook ingestion is in scope for this milestone.

---

## Minor Pitfalls

---

### Pitfall 12: Redis Ephemeral Storage on Free Tiers Loses Celery Task Results

**What goes wrong:** `celery_result_backend` is `redis://localhost:6379/1` (a different Redis DB from the broker). Free-tier Redis on Render is ephemeral — data is lost on restart. If a Celery task completes and the result is stored in Redis, a Redis restart between task completion and `finalise_session()` causes the task result to disappear. However, GRASP's architecture avoids this: `finalise_session()` is called inside the task itself before it ends, writing directly to Postgres. The Celery result backend is not used for anything critical.

**Prevention:** The architecture is already correct. No action required. But confirm the Celery result backend is not being polled anywhere in the codebase to confirm task completion — polling the result backend would break on Redis restart.

**Detection:** `celery_app.AsyncResult(task_id).get()` timeouts. Not currently used in GRASP routes, so this is low risk.

**Deploy phase:** Non-blocking. Awareness item only.

---

### Pitfall 13: X-User-ID Legacy Auth Header Is Enabled in Production

**What goes wrong:** `core/auth.py` (line 71) accepts `X-User-ID: <uuid>` as an authentication method, bypassing JWT entirely. This was a development convenience. In production, any user who knows another user's UUID can authenticate as them.

**Why it happens:** The "deprecated, will be removed" comment in `auth.py` has not been acted on.

**Consequences:** Authentication bypass for any user whose UUID is known (UUIDs are returned in API responses).

**Prevention:**
- Remove the `X-User-ID` fallback before production deploy. It is a two-line deletion in `auth.py` (the `elif x_user_id:` block).
- If keeping it for emergency admin use, gate it on `app_env == "development"` at minimum.

**Detection:** `curl -H "X-User-ID: <any-valid-uuid>" https://your-api.com/api/v1/sessions` returns 200.

**Deploy phase:** Security hardening phase. Must be done before sharing the URL.

---

### Pitfall 14: Polling at 2-Second Interval Amplifies Cold-Start Costs

**What goes wrong:** `usePolling.ts` defaults to a 2-second interval. For a session in `GENERATING` status, the frontend polls `GET /sessions/{id}` every 2 seconds. Each poll on an in-progress session hits the `status_projection()` slow path, which queries the LangGraph checkpoint table in Postgres. For a 5–10 minute pipeline run, that is 150–300 checkpoint queries per session. On a free-tier Postgres with 25 connection limit, 3 concurrent active sessions = 450–900 checkpoint reads in 10 minutes — near the connection limit.

**Prevention:**
- Increase the polling interval to 5 seconds for in-progress sessions (the pipeline takes 3–8 minutes, 1 second granularity adds no value).
- Or implement exponential backoff: start at 2s, increase to 10s after 60s of polling.
- The architecture already has the fast path for terminal sessions (reads the DB row directly) — polling cost only applies to in-progress sessions.

**Detection:** Postgres `pg_stat_activity` shows many short-lived queries to `checkpoints` table.

**Deploy phase:** Post-deploy optimization. Non-blocking for 2-5 users, but address before expanding.

---

### Pitfall 15: Alembic `alembic.ini` Contains Localhost Fallback URL

**What goes wrong:** `alembic.ini` has `sqlalchemy.url = postgresql+psycopg://grasp:grasp@localhost:5432/grasp` as a hardcoded fallback. If someone runs `alembic upgrade head` from a CI/CD context or on the host machine without `DATABASE_URL` set, Alembic tries to migrate localhost, gets a connection error, and either fails or (if localhost happens to have a Postgres) migrates the wrong database.

**Prevention:**
- Remove the hardcoded URL from `alembic.ini` and require `DATABASE_URL` to be set as an environment variable for all migration runs.
- In `alembic/env.py`, raise a clear error if the settings URL is not set.

**Detection:** Running `alembic current` outside a configured environment returns an error about localhost connection refused, or worse, connects to an unexpected local database.

**Deploy phase:** Pre-deploy setup. Low risk for this milestone since migrations run via the app lifespan.

---

## Phase-Specific Warnings

| Deploy Phase | Likely Pitfall | Mitigation |
|---|---|---|
| Hosting platform selection | pgvector/extension availability | Choose Supabase or Neon — both support extensions on free tier |
| Environment variable setup | JWT secret uses default | Set `APP_ENV=production`, `JWT_SECRET_KEY`, verify startup log |
| Environment variable setup | CORS origins malformed JSON | Set as `'["https://frontend-url"]'` — outer single quotes, inner double quotes |
| Architecture decision | Frontend relative URL broken | Serve React static build from FastAPI, or set `VITE_API_BASE_URL` |
| Worker deployment | Concurrency OOM on free tier | Set `CELERY_WORKER_CONCURRENCY=1`, use `--pool=solo` |
| Security review | X-User-ID legacy auth active | Delete the `elif x_user_id:` block from `auth.py` before going live |
| Ingestion feature | macOS OCR not available on Linux | Disable ingestion or add Linux OCR fallback |
| Migration setup | LangGraph tables break autogenerate | Add `include_object` filter in `alembic/env.py` |
| Post-deploy | Cold starts kill frontend requests | Add UptimeRobot ping, use persistent Redis (Upstash) |

---

## Confidence Assessment

| Area | Confidence | Basis |
|---|---|---|
| JWT default secret risk | HIGH | Confirmed in `core/settings.py` and `main.py` lifespan guard — code-verified |
| pgvector extension limits | HIGH | Well-documented free-tier constraint; Supabase/Neon known to support it |
| CORS list parsing | HIGH | pydantic-settings JSON list parsing is a known gotcha — code-verified |
| Celery concurrency OOM | HIGH | Memory footprint of LangChain + asyncpg per worker is well-characterized |
| Relative URL production break | HIGH | Code-verified — `client.ts` uses `/api/v1` with no env variable override |
| X-User-ID auth bypass | HIGH | Confirmed active in `auth.py` — trivial to exploit |
| pyobjc Linux failure | HIGH | Platform guard confirmed in `requirements.txt` — OCR code path macOS-only |
| LangGraph + Alembic table conflict | MEDIUM | Known issue with LangGraph OSS — confirmed tables are not filtered in this project |
| Alembic localhost fallback | MEDIUM | Confirmed in `alembic.ini` — risk depends on deploy process |
| Polling interval cost | MEDIUM | Connection math is straightforward — Postgres limits are platform-specific |

---

## Sources

- Codebase analysis: `core/settings.py`, `core/auth.py`, `main.py`, `workers/celery_app.py`, `workers/tasks.py`, `frontend/src/api/client.ts`, `frontend/src/hooks/usePolling.ts`, `alembic/versions/`, `requirements.txt`
- Architecture pattern: LangGraph PostgresSaver creates its own DDL outside Alembic's control — confirmed in `main.py` `checkpointer.setup()` call
- Known platform limits: Render free tier 512 MB RAM, Supabase free tier 25 connections, Neon free tier 100 connections — well-documented platform constraints (MEDIUM confidence from training data; verify on platform docs before selecting host)
