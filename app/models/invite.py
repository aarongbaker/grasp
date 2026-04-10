"""
app/models/invite.py
Invite codes for controlled registration.

When INVITE_CODES_ENABLED=True, registration requires a valid invite.
Each invite is single-use, email-specific, and atomically claimed during
registration to prevent race conditions.
"""

from datetime import datetime, timedelta, timezone

from sqlmodel import Field, SQLModel


class Invite(SQLModel, table=True):
    __tablename__ = "invites"

    # The invite code itself is the primary key — it's what users share/enter.
    # Using it as PK avoids a secondary unique index and simplifies lookups.
    code: str = Field(primary_key=True, index=True)

    # Invite is locked to a specific email — can't be forwarded to another address.
    # The registration endpoint verifies body.email == invite.email before claiming.
    email: str = Field(index=True)

    # Set atomically when claimed during registration. None = still valid.
    # The registration handler checks claimed_at is None AND expires_at > now
    # before creating the user, then sets claimed_at in the same transaction
    # to prevent race conditions under concurrent registration attempts.
    claimed_at: datetime | None = Field(default=None)

    # Timestamp when the invite was generated (for audit/admin display)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    # Invites expire after 7 days by default. The registration endpoint checks
    # this before claiming — expired invites are rejected even if unclaimed.
    # Stored as UTC naive for consistency with other models.
    expires_at: datetime = Field(
        default_factory=lambda: (datetime.now(timezone.utc) + timedelta(days=7)).replace(tzinfo=None)
    )
