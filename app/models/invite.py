"""
app/models/invite.py
Invite codes for controlled registration.

When INVITE_CODES_ENABLED=True, registration requires a valid invite.
Each invite is single-use, email-specific, and atomically claimed during
registration to prevent race conditions.
"""

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class Invite(SQLModel, table=True):
    __tablename__ = "invites"

    code: str = Field(primary_key=True, index=True)
    email: str = Field(index=True)
    claimed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
