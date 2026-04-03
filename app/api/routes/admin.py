"""app/api/routes/admin.py — Admin-only operational endpoints."""

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

from app.core.auth import ensure_admin_user
from app.core.deps import CurrentUser, DBSession
from app.models.invite import Invite

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


class CreateInviteRequest(BaseModel):
    email: EmailStr


class InviteResponse(BaseModel):
    code: str
    email: str
    claimed_at: datetime | None = None
    created_at: datetime


def _generate_invite_code() -> str:
    """Return a high-entropy operator-safe invite code."""
    return secrets.token_urlsafe(18)


@router.post("/invites", status_code=201, response_model=InviteResponse)
async def create_invite(body: CreateInviteRequest, db: DBSession, current_user: CurrentUser):
    """Issue an invite for the requested email. Only configured admins may call this."""
    ensure_admin_user(current_user)

    invite = Invite(
        code=_generate_invite_code(),
        email=body.email,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    logger.info("admin invite issued", extra={"invite_email": invite.email, "admin_user_id": str(current_user.user_id)})

    return InviteResponse.model_validate(invite, from_attributes=True)
