"""api/routes/health.py — GET /api/v1/health"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from db.session import get_session

router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_session)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "connected"}
