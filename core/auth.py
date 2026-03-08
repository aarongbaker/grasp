"""
core/auth.py
V1: X-User-ID header placeholder.
V2: Swap to Authorization: Bearer <jwt>. Only this file changes.
Every route function stays identical — the dependency injection pattern
was designed for this upgrade from day one.

Headers > query params: query params appear in server logs; headers are
less exposed. This matters especially for user IDs.
"""

import uuid
from fastapi import Header, HTTPException, Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from db.session import get_session
from models.user import UserProfile
from sqlmodel import select


async def get_current_user(
    x_user_id: str = Header(..., description="Chef's user UUID. V2: JWT Bearer token."),
    db: AsyncSession = Depends(get_session),
) -> UserProfile:
    """
    V1 auth dependency. Reads X-User-ID header, queries Postgres, returns UserProfile.
    Injected into every route that needs ownership checks.
    """
    try:
        user_id = uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-User-ID must be a valid UUID")

    result = await db.exec(select(UserProfile).where(UserProfile.user_id == user_id))
    user = result.first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user
