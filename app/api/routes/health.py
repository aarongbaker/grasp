"""api/routes/health.py — GET /api/v1/health

Liveness + readiness probe in one endpoint.

Why check the DB instead of just returning 200?
  A plain 200 tells the load balancer the process is alive, but not whether
  it can serve traffic. Including a DB round-trip (SELECT 1) makes this a
  readiness probe — if the DB connection pool is exhausted or the DB is
  unreachable, this returns a 500, and the load balancer routes around the
  instance until it recovers.

  SELECT 1 is the lightest possible query — no table scan, no lock, no
  plan — but it exercises the full connection path: pool acquisition,
  network, DB process, response parsing. That's exactly what we need.

Why not check Pinecone, Redis, Celery?
  Deep health checks (all dependencies) add latency and failure surface to
  every probe. If Redis is down, sessions still work — the rate limiter
  falls back to in-memory. Pinecone is only needed for enrichment, not
  for basic session creation. A shallow DB check is the right balance:
  fast, reliable, and tells us the critical path is live.

Note: this route has no rate limit because it's called by the load balancer
every 10-30 seconds. Adding a rate limit would cause the probe to 429
itself under normal load.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.session import get_session

router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_session)):
    # Execute a trivial query to verify the DB connection pool is healthy.
    # If this raises, FastAPI returns a 500 — the load balancer stops
    # routing to this instance until the probe recovers.
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "connected"}
