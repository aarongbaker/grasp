# Railway + Cloudflare deploy guide for GRASP

This is the beginner version.

If you have never used Railway or Cloudflare before, follow this in order:

1. Deploy the backend API to Railway
2. Deploy the worker to Railway
3. Deploy the frontend to Cloudflare Pages
4. Connect the frontend to the backend with environment variables
5. Verify the full flow works

---

## What goes where

### Railway
Use Railway for the backend services:
- FastAPI API server
- Celery worker
- Postgres database
- Redis

### Cloudflare Pages
Use Cloudflare Pages for the frontend:
- Vite/React app in `frontend/`

---

## Before you start

You will need accounts for:
- Railway
- Cloudflare
- GitHub

You will also need these secrets available:
- `JWT_SECRET_KEY`
- `DATABASE_URL`
- `LANGGRAPH_CHECKPOINT_URL`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `CORS_ALLOWED_ORIGINS`
- `ANTHROPIC_API_KEY`
- `VITE_API_URL` (Cloudflare side, added later)

Important project-specific notes:
- `CORS_ALLOWED_ORIGINS` must be a **JSON array string**, not a bare URL.
  - Good: `["https://your-site.pages.dev"]`
  - Bad: `https://your-site.pages.dev`
- This project needs **two Postgres-style URLs**:
  - `DATABASE_URL` for the API
  - `LANGGRAPH_CHECKPOINT_URL` for LangGraph checkpoint persistence
- The worker must run with:
  - `--pool=solo --concurrency=1`

---

## Step 1: Put the code on GitHub

If the repo is not already on GitHub:

1. Create a GitHub repository
2. Push this project to it
3. Confirm the repo includes both backend code and the `frontend/` folder

Railway and Cloudflare Pages both deploy most easily from GitHub.

---

## Step 2: Create a Railway project

1. Log in to Railway
2. Create a new project
3. Choose the option to deploy from GitHub
4. Connect the GRASP repository

At this point, Railway may try to create one service automatically from the repo. That is fine. We will end up with:
- one API service
- one worker service
- one Postgres service
- one Redis service

---

## Step 3: Add Postgres and Redis in Railway

Inside the Railway project:

1. Add a **Postgres** service
2. Add a **Redis** service

Railway will provision both and expose connection values.

You will use these connection values for environment variables on the API and worker services.

---

## Step 4: Create the Railway API service

This service runs the FastAPI backend.

### Service setup

1. In Railway, create or select the service built from this repo
2. Make sure it points at the root of the repository
3. Let it use the project `Dockerfile`

### Start command

The project Dockerfile already supports Railway's dynamic port handling.
You should not hardcode a port in Railway.

### Environment variables for the API service

Set these in Railway for the API service:

- `APP_ENV=production`
- `JWT_SECRET_KEY=<your strong generated secret>`
- `DATABASE_URL=<Railway Postgres URL using asyncpg scheme if required by app config>`
- `LANGGRAPH_CHECKPOINT_URL=<Postgres URL for checkpoint persistence using psycopg-style scheme>`
- `REDIS_URL=<Railway Redis URL>`
- `CELERY_BROKER_URL=<Railway Redis URL for broker, usually /0>`
- `CELERY_RESULT_BACKEND=<Railway Redis URL for results, usually /1>`
- `CORS_ALLOWED_ORIGINS=["https://<your-cloudflare-pages-domain>"]`
- `ANTHROPIC_API_KEY=<your key>`

### Very important: database URL formats

This repo has a known requirement:
- `DATABASE_URL` is used by the API stack
- `LANGGRAPH_CHECKPOINT_URL` is used by checkpoint persistence

They point to the same Postgres instance, but may need different URL schemes.

Project knowledge note:
- `DATABASE_URL` uses the async driver path for the API
- `LANGGRAPH_CHECKPOINT_URL` uses the psycopg3-compatible path for LangGraph checkpointing

If you copy one URL into both variables without adjusting the scheme, startup can fail or checkpointing can break.

---

## Step 5: Deploy the API and verify health

After setting env vars:

1. Trigger a deploy in Railway
2. Wait for the service to become active
3. Copy the public Railway service URL
4. Open this in your browser or use curl:

```bash
curl -s https://<railway-url>/api/v1/health
```

Expected result:

```json
{"status":"ok","db":"connected"}
```

If that works, the API is up.

### If the API does not start

Check Railway logs for these common problems:

- `JWT_SECRET_KEY` missing or default-like in production
- `DATABASE_URL` wrong
- `LANGGRAPH_CHECKPOINT_URL` wrong
- `REDIS_URL` wrong
- `CORS_ALLOWED_ORIGINS` not valid JSON
- migrations or startup dependency failures

Common failure examples:
- Health says database disconnected -> Postgres URL is wrong
- App crashes on startup -> production env var or checkpoint config problem
- Browser frontend later shows CORS errors -> `CORS_ALLOWED_ORIGINS` is wrong

---

## Step 6: Create the Railway worker service

This project needs a separate worker service.
The worker processes background planning jobs.

### Create the worker

1. In the same Railway project, add another service from the same GitHub repo
2. Use the same codebase / same Docker image source
3. Override the start command so it runs the Celery worker instead of the API

Use this worker command:

```bash
celery -A app.workers.celery_app worker --concurrency=1 --pool=solo --loglevel=INFO
```

This is important for Railway Hobby-sized memory limits.
Do not raise concurrency unless you know the memory budget can handle it.

### Environment variables for the worker

Use the same relevant env vars as the API service:

- `APP_ENV=production`
- `JWT_SECRET_KEY=<same value>`
- `DATABASE_URL=<same DB>`
- `LANGGRAPH_CHECKPOINT_URL=<same checkpoint DB URL>`
- `REDIS_URL=<same Redis>`
- `CELERY_BROKER_URL=<same Redis broker URL>`
- `CELERY_RESULT_BACKEND=<same Redis result URL>`
- `ANTHROPIC_API_KEY=<same key>`

The worker must be able to reach the same Postgres and Redis services as the API.

---

## Step 7: Verify the worker is healthy

After the worker deploys:

1. Open the worker logs in Railway
2. Confirm it starts without connection errors
3. Look for obvious failures connecting to Redis or Postgres

What you want:
- worker starts successfully
- worker stays running
- no repeated crash loop

If the worker starts but jobs never complete later, the first things to re-check are:
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `DATABASE_URL`
- `LANGGRAPH_CHECKPOINT_URL`
- whether the worker command is exactly `--concurrency=1 --pool=solo`

---

## Step 8: Create a Cloudflare Pages project

Now deploy the frontend.

1. Log in to Cloudflare
2. Go to **Workers & Pages**
3. Create a new **Pages** project
4. Connect your GitHub repository
5. Select this repo

---

## Step 9: Configure the Cloudflare build

Use these settings:

- **Build command:** `cd frontend && npm ci && npm run build`
- **Output directory:** `frontend/dist`

Important:
- `VITE_API_URL` must be set at **build time**
- Vite inlines env vars during build
- Do not assume runtime env vars will fix it later

### Add frontend environment variable

In Cloudflare Pages project settings, add:

- `VITE_API_URL=https://<your-railway-api-url>`

Use the Railway API URL only.
Do not add `/api/v1` to the env var.
The frontend code already appends `/api/v1`.

Good:
- `https://grasp-api.up.railway.app`

Bad:
- `https://grasp-api.up.railway.app/`
- `https://grasp-api.up.railway.app/api/v1`

---

## Step 10: Update backend CORS to allow Cloudflare

Once Cloudflare gives you the Pages domain, go back to Railway and update:

- `CORS_ALLOWED_ORIGINS`

Example:

```env
CORS_ALLOWED_ORIGINS=["https://grasp-abc123.pages.dev"]
```

If you later add a custom domain, update this variable again to include that domain too.

If CORS is wrong, the frontend may load but all API requests will fail in the browser.

---

## Step 11: Deploy the frontend on Cloudflare Pages

After build settings and `VITE_API_URL` are configured:

1. Trigger the first Pages deploy
2. Wait for it to finish
3. Open the `*.pages.dev` URL

You should see the GRASP frontend load.

---

## Step 12: Verify routing works

Check these paths in the browser:

- `/`
- `/login`
- `/register`

Also refresh the page while you are on `/login`.

Expected result:
- the app still loads
- no 404 on refresh

If you do hit a refresh 404, add a `_redirects` file later with:

```text
/* /index.html 200
```

Project notes suggested Cloudflare Pages should handle this automatically when no `404.html` is present, but this fallback rule is still a good fix if routing breaks.

---

## Step 13: Run the real end-to-end check

Once API, worker, and frontend are all deployed:

1. Open the Cloudflare Pages URL
2. Register a new user
3. Log in
4. Create a new session
5. Submit a meal request
6. Wait for the session to progress
7. Open the result page
8. Confirm the Gantt/schedule view appears

Expected result:
- registration works
- login works
- session starts
- polling updates status
- session completes
- Gantt/timeline renders

---

## Troubleshooting

## Frontend loads but API calls fail

Likely causes:
- `VITE_API_URL` is wrong
- `CORS_ALLOWED_ORIGINS` is wrong
- Railway API is not healthy

Check:
- browser network tab
- Railway API logs
- Cloudflare Pages env var value

---

## API health endpoint fails

Test:

```bash
curl -s https://<railway-url>/api/v1/health
```

If it fails, check:
- `DATABASE_URL`
- app startup logs
- whether Postgres service is actually running

---

## Worker is running but jobs never finish

Check worker logs for:
- Redis connection errors
- Postgres/checkpoint errors
- crash loops
- missing `ANTHROPIC_API_KEY`

Re-check:
- `REDIS_URL`
- `DATABASE_URL`
- `LANGGRAPH_CHECKPOINT_URL`
- worker command

Expected worker command:

```bash
celery -A app.workers.celery_app worker --concurrency=1 --pool=solo --loglevel=INFO
```

---

## Cloudflare deploy succeeds but app still points to the wrong API

This usually means `VITE_API_URL` was wrong at build time.

Fix:
1. Correct `VITE_API_URL` in Cloudflare Pages settings
2. Trigger a new deployment

Remember: Vite bakes the value into the built frontend.

---

## CORS error in browser console

Your backend CORS value must be JSON, for example:

```env
CORS_ALLOWED_ORIGINS=["https://grasp-abc123.pages.dev"]
```

Not this:

```env
CORS_ALLOWED_ORIGINS=https://grasp-abc123.pages.dev
```

---

## Minimum deployment checklist

### Railway API
- [ ] Postgres added
- [ ] Redis added
- [ ] API service deployed
- [ ] `APP_ENV=production`
- [ ] `JWT_SECRET_KEY` set
- [ ] `DATABASE_URL` set
- [ ] `LANGGRAPH_CHECKPOINT_URL` set
- [ ] `REDIS_URL` set
- [ ] `CELERY_BROKER_URL` set
- [ ] `CELERY_RESULT_BACKEND` set
- [ ] `ANTHROPIC_API_KEY` set
- [ ] Health check passes

### Railway worker
- [ ] Worker service created from same repo
- [ ] Worker command set to `celery -A app.workers.celery_app worker --concurrency=1 --pool=solo --loglevel=INFO`
- [ ] Same DB/Redis/provider env vars set
- [ ] Worker logs show successful startup

### Cloudflare Pages
- [ ] Pages project connected to repo
- [ ] Build command set to `cd frontend && npm ci && npm run build`
- [ ] Output directory set to `frontend/dist`
- [ ] `VITE_API_URL` set to Railway API base URL
- [ ] Pages site deploys successfully
- [ ] `/login` refresh works

### Final verification
- [ ] Register works
- [ ] Login works
- [ ] Session creation works
- [ ] Session completes
- [ ] Gantt result renders

---

## Source notes

This guide was consolidated from:
- `.gsd/milestones/M002/slices/S01/S01-UAT.md`
- `.gsd/milestones/M002/slices/S02/S02-PLAN.md`
- `.gsd/milestones/M002/slices/S02/S02-RESEARCH.md`
- `.gsd/milestones/M002/slices/S03/S03-UAT.md`
- `.gsd/milestones/M002/slices/S03/S03-RESEARCH.md`
