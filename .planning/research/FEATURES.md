# Feature Landscape: GRASP v1.1 Deployment

**Domain:** Production deployment of an existing local-only web app for small friend beta (2-5 users)
**Researched:** 2026-03-19
**Confidence:** HIGH — based on direct codebase inspection, not guesswork

---

## What Already Exists (Do Not Rebuild)

Before defining what needs to be built, these deployment-adjacent features are already complete:

| Feature | Evidence |
|---------|----------|
| User registration with kitchen config | `POST /api/v1/users` + `RegisterPage.tsx` |
| Login / logout with JWT | `POST /api/v1/auth/token` + `LoginPage.tsx` |
| Token refresh (silent, automatic) | `api/client.ts` — 401 retry with refresh |
| Auth expiry → redirect to login | `grasp:auth-expired` custom event in client |
| Protected routes (redirect if not auth) | `AppShell.tsx` — redirects to `/welcome` |
| Landing page at `/welcome` | `LandingPage.tsx` with nav, hero, features |
| Health check endpoint | `GET /api/v1/health` — DB ping, returns `{status: ok}` |
| CORS middleware | `main.py` — reads `cors_allowed_origins` from settings |
| Rate limiting | slowapi — Redis-backed with in-memory fallback |
| JWT secret validation at startup | `lifespan()` — crashes if default secret in production |
| Error surfacing (API errors inline) | Dashboard + session detail pages |
| Session delete | `DELETE /api/v1/sessions/{id}` + dashboard UI |

---

## Table Stakes

Features without which the app cannot be used by friends on the internet.

| Feature | Why Required | Complexity | Current Gap |
|---------|--------------|------------|-------------|
| Backend Dockerfile | App needs to run somewhere | Low | No Dockerfile exists |
| Frontend Dockerfile (or build pipeline) | React SPA must be built and served | Low | No Dockerfile exists |
| Production docker-compose | Compose the full stack: API + Celery worker + Postgres + Redis | Medium | Existing compose is dev-only (no Celery worker, hardcoded `grasp:grasp` credentials) |
| pgvector extension in Postgres | RAG pipeline depends on it — app will crash without it | Low | Not in current compose; needs `CREATE EXTENSION vector` on DB init |
| Environment variable documentation (`.env.example`) | Friends cannot configure secrets they cannot see | Low | No `.env.example` exists |
| CORS configured for production origin | API rejects all browser requests from deployed URL | Low | `cors_allowed_origins` hardcoded to `localhost` in settings default |
| Frontend API URL configuration | `client.ts` uses `/api/v1` (relative) — works only when frontend is co-located or proxied | Low | Vite proxy handles dev; prod needs either co-location or `VITE_API_BASE` env var |
| Static file serving strategy | React build output must be served — Vite dev server is not a production server | Medium | No serving strategy decided. Options: FastAPI serves `/dist`, or Nginx, or CDN |
| SPA 404 fallback | React Router requires all non-API paths to return `index.html` | Low | Not configured — direct URL access to `/sessions/123` breaks on hard refresh |
| Secret generation instructions | `JWT_SECRET_KEY`, DB password, Redis password — friends must know how to generate | Low | Startup already validates JWT secret; needs documented command |
| Account creation for beta users | Friends need accounts — registration is open but not discoverable via a link | Low | Register page exists at `/register`; just needs the URL shared. Or add invite code. |

---

## Differentiators

Features that would improve the beta experience without being strictly required.

| Feature | Value | Complexity | Notes |
|---------|-------|------------|-------|
| Invite code / registration gate | Prevents random internet strangers from using API keys you are paying for | Low | Single env var `INVITE_CODE`; check on `POST /users`. Can be omitted if URL not shared publicly. |
| Friendly 404 page | Unauthenticated deep link (e.g. `/sessions/abc`) shows blank or crashes; a graceful 404 feels polished | Low | React Router `*` catch route. 30 min to add. |
| Admin account seed script | One-liner to create the first user without running the app in a browser | Low | `python scripts/seed_user.py --email x --password y`. Useful for first deploy. |
| Health check includes Redis and Celery | Current `/health` only pings DB; a broken Celery worker produces silent failures on session creation | Medium | Extend health endpoint to also `PING` Redis. |
| `prefers-reduced-motion` already in CLAUDE.md | Already specified — just verify it is implemented in CSS | Low | Design system already calls for it. |
| Graceful "pipeline still running" UX | Session polling works locally; under high latency on free-tier hosting, skeleton loaders prevent confusion | Low | Already has skeleton loaders per CLAUDE.md; verify timeout value in `client.ts` (currently 30s — may be too short for cold-start hosts) |

---

## Anti-Features

Features to explicitly not build for this milestone.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Email verification | Adds email provider (SendGrid, SES) + complexity. Zero value for 5 known friends. | Just share the register URL directly. |
| Password reset flow | Same: requires email. Friends can DM you. | Document: "DM me to reset your password" |
| CI/CD pipeline | Out of scope per PROJECT.md. | Manual `git pull && docker compose up --build -d` on the server. |
| Monitoring / alerting | Out of scope per PROJECT.md. | Watch logs manually: `docker compose logs -f api`. |
| Custom domain | Out of scope per PROJECT.md. | Platform subdomain is fine for 5 friends. |
| Multi-region / CDN | No meaningful load. | Single instance is fine. |
| Database backups | Nice but not critical for a beta that holds no production data. | Document: "Data is ephemeral in beta" |
| Admin dashboard | No management UI needed for 5 users. | SSH + psql if needed. |
| Rate limit tuning for production | Default slowapi limits are fine for 2-5 users. | Leave defaults. |

---

## Feature Dependencies

```
pgvector extension
  → Postgres container init script / custom image
  → Alembic migrations (already depend on pgvector)

CORS production origin
  → Known deployed URL (chicken-and-egg: deploy first, then set)
  → Workaround: use wildcard "*" for initial test, tighten after URL is known

Static file serving strategy
  → Frontend Dockerfile
  → Determines whether Nginx is in the stack or not

Frontend API URL
  → Static file serving strategy
  → If frontend is served by FastAPI (same origin), /api/v1 relative path works out of the box
  → If frontend is on a separate host (CDN), VITE_API_BASE must be set at build time

Account seed script
  → DB running and migrated
  → Only needed if you want accounts before sharing the URL

Invite code gate (optional)
  → Env var added to settings
  → Register endpoint reads it before creating user
```

---

## MVP Recommendation

The minimum to make the app usable by friends is:

1. **Backend Dockerfile** — Build the FastAPI + Celery image. Single Dockerfile, two `CMD` targets or compose override for worker.
2. **Production docker-compose** — `docker-compose.prod.yml` with: Postgres (pgvector image), Redis, API, Celery worker. Real credentials from env file.
3. **pgvector Postgres image** — Use `pgvector/pgvector:pg16` instead of `postgres:16-alpine`. Runs `CREATE EXTENSION vector` via init SQL.
4. **`.env.example`** — Document every required env var with a placeholder and the command to generate secrets.
5. **CORS production origin** — One env var: `CORS_ALLOWED_ORIGINS=https://your-app.fly.dev`. Already wired into settings.
6. **SPA 404 fallback** — One Nginx `try_files` directive or one FastAPI static mount + catch-all route. Needed for any deep link to work.
7. **Serving strategy decision** — Simplest for free-tier: serve frontend static files from FastAPI using `StaticFiles` + a catch-all that returns `index.html`. Avoids Nginx as a separate container.

**Defer:**
- Invite code gate: only needed if the URL might become public. For 5 friends sharing a link, probably unnecessary.
- Admin seed script: registration page exists — just share the URL.
- Extended health check (Redis ping): useful but not blocking.

---

## Complexity Notes

| Feature | Estimate | Blocker? |
|---------|----------|----------|
| Backend Dockerfile | 1-2h | No — straightforward FastAPI + Python image |
| Production docker-compose | 1-2h | No — adapt existing dev compose |
| pgvector Postgres image swap | 15m | No — drop-in image replacement |
| `.env.example` | 30m | No — documentation only |
| CORS env var wire-up | 15m | No — settings already reads from env |
| SPA 404 fallback + static serving | 1-2h | Yes — must decide serving strategy first |
| Invite code gate | 1h | No — single env var + one check in users route |
| Admin seed script | 30m | No — standalone Python script |

Total estimated work for the must-have list: **4-8 hours** depending on serving strategy choice.

---

## Sources

All findings based on direct inspection of:
- `/main.py` — CORS, rate limiting, lifespan, health wiring
- `/core/settings.py` — all env vars and their defaults
- `/api/routes/health.py` — existing health check scope
- `/api/routes/auth.py`, `/api/routes/users.py` — existing auth flow
- `/frontend/src/api/client.ts` — API base URL, timeout, refresh logic
- `/frontend/src/pages/RegisterPage.tsx`, `LoginPage.tsx` — existing signup flow
- `/frontend/src/components/layout/AppShell.tsx` — auth guard behavior
- `/frontend/vite.config.ts` — dev proxy config (reveals prod URL gap)
- `/docker-compose.yml` — current dev-only compose (reveals what is missing for prod)
- `/.planning/PROJECT.md` — milestone goals and explicit out-of-scope list
