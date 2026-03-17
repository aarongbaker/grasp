"""
core/deps.py
Typed dependency shorthands for FastAPI route functions.
DBSession, AppSettings, CurrentUser — import these in routes, not the
raw get_session/get_settings/get_current_user functions.
"""

from typing import Annotated

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from core.auth import get_current_user
from core.settings import Settings, get_settings
from db.session import get_session
from models.user import UserProfile

DBSession = Annotated[AsyncSession, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]
CurrentUser = Annotated[UserProfile, Depends(get_current_user)]
