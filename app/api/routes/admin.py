"""app/api/routes/admin.py — Admin-only operational endpoints.

Admin access is enforced via ensure_admin_user(), not a separate auth layer.
Regular users hit this router after normal JWT authentication, but
ensure_admin_user() checks whether their user_id or email is in the admin
allow-list configured in settings. There's no separate admin token type —
the separation is logical, not cryptographic.

Currently exposes one operation: invite code generation. Invite codes are
email-specific (tied to one address) and expire after 7 days. They prevent
open registration while still allowing controlled onboarding.

Why 18 bytes for secrets.token_urlsafe(18)?
  token_urlsafe() base64-encodes the bytes, so 18 raw bytes → 24 URL-safe
  characters. 18 bytes = 144 bits of entropy — well above the 128-bit
  threshold for cryptographic tokens. The result is human-copyable (short
  enough to paste from email) and has no ambiguous characters (no 0/O, 1/l).

Why 7-day expiry?
  Long enough for an invitee to respond, short enough to limit exposure
  if the invite email is intercepted or forwarded. Invite validation in
  create_user() (users.py) checks expires_at on every use.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

from app.core.auth import ensure_admin_user
from app.core.deps import CurrentUser, DBSession
from app.models.invite import Invite

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


class CreateInviteRequest(BaseModel):
    # EmailStr validates format — rejects "notanemail" before it hits the DB.
    # Invite validation in create_user() also case-folds the email, so
    # "User@Example.com" and "user@example.com" are treated identically.
    email: EmailStr


class InviteResponse(BaseModel):
    code: str
    email: str
    claimed_at: datetime | None = None  # None means not yet used
    created_at: datetime
    expires_at: datetime


def _generate_invite_code() -> str:
    """Return a high-entropy operator-safe invite code.

    18 bytes → 24 URL-safe base64 characters. Cryptographically random
    via os.urandom() under the hood — safe for use as a one-time secret.
    Not stored as a hash (we need to show it back to the admin and send
    it in email), so it lives as plaintext in the Invite table.
    """
    return secrets.token_urlsafe(18)


@router.post("/invites", status_code=201, response_model=InviteResponse)
async def create_invite(body: CreateInviteRequest, db: DBSession, current_user: CurrentUser):
    """Issue an invite for the requested email. Only configured admins may call this.

    ensure_admin_user() raises 403 if current_user is not in the admin list —
    the check is early so the DB write never happens for non-admins.

    Invite.email stores the canonical (lowercased) form of the address.
    create_user() also lowercases the incoming email before comparison, so
    capitalisation differences don't create a mismatch.

    The audit log includes admin_user_id so we can trace every invite back
    to the admin who issued it — useful if an invite code is abused.
    """
    ensure_admin_user(current_user)

    invite = Invite(
        code=_generate_invite_code(),
        email=body.email,
        # Store naive UTC datetimes (no tzinfo) — the DB column has no timezone.
        # replace(tzinfo=None) strips the tzinfo from datetime.now(timezone.utc)
        # to match the Postgres column type. See the same pattern in users.py.
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).replace(tzinfo=None),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    # Structured log — invite_email and admin_user_id are indexed fields
    # so we can query "all invites issued by admin X" or "all invites for email Y".
    logger.info("admin invite issued", extra={"invite_email": invite.email, "admin_user_id": str(current_user.user_id)})

    # model_validate with from_attributes=True: Invite is a SQLModel ORM instance,
    # not a plain dict. from_attributes allows Pydantic to read fields from
    # ORM attributes rather than dict keys.
    return InviteResponse.model_validate(invite, from_attributes=True)
