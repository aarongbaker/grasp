"""
core/deps.py
Typed dependency shorthands for FastAPI route functions.

DBSession, AppSettings, CurrentUser — import these in routes, not the
raw get_session/get_settings/get_current_user functions.

Why type aliases instead of direct Depends() calls in routes?
  1. Concise route signatures — `db: DBSession` vs `db: AsyncSession = Depends(get_session)`
  2. Single place to change if the underlying dependency changes
  3. Type checkers see the resolved type (AsyncSession, Settings, UserProfile)
     rather than the opaque Depends() expression

CurrentUser is the most important alias: it injects both the DB session
(for the user lookup) and the auth check (JWT decode + DB existence verify).
Any route that declares `current_user: CurrentUser` is automatically
authenticated — no route needs to manually call get_current_user().
"""

from typing import Annotated

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.settings import Settings, get_settings
from app.db.session import get_session
from app.models.user import UserProfile

# One async database session per request. FastAPI creates a new session
# at the start of each request and closes it after the response is sent.
# The session is not shared across requests — each request gets its own
# transaction scope from the SessionLocal factory in db/session.py.
DBSession = Annotated[AsyncSession, Depends(get_session)]

# The settings singleton. get_settings() uses @lru_cache so this is
# effectively free after the first call — no re-reading .env per request.
AppSettings = Annotated[Settings, Depends(get_settings)]

# The authenticated user. Requires a valid JWT Bearer token in the
# Authorization header. Raises HTTP 401 if missing/invalid/expired.
# Raises HTTP 404 if the token's user no longer exists in the database.
# Any route that declares `current_user: CurrentUser` is automatically
# protected — authentication is enforced by the dependency, not the route.
CurrentUser = Annotated[UserProfile, Depends(get_current_user)]
