"""
models/session.py
Session SQLModel → Postgres.

Session.status has exactly two direct writers (§2.9 V1.6):
  1. POST /sessions/{id}/run — writes GENERATING once at enqueue
  2. finalise_session() in core/status.py — writes terminal status at end

In-progress statuses (ENRICHING, VALIDATING, SCHEDULING) are never written
to the Session row. They are derived live from the LangGraph checkpoint
by status_projection() and returned to the frontend on polling.

The two-tier read in GET /sessions/{id}:
  terminal status → read Session row directly (fast, indexed)
  in-progress     → call status_projection() (reads checkpoint)
Never write in-progress status back to the row.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, String
from sqlmodel import Column, Field, SQLModel

from app.models.enums import SessionStatus


class Session(SQLModel, table=True):
    __tablename__ = "sessions"

    session_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)
    status: SessionStatus = Field(default=SessionStatus.PENDING, sa_column=Column(String, nullable=False, index=True))

    # DinnerConcept stored as JSON — pure Pydantic, not a separate table
    concept_json: dict = Field(default_factory=dict, sa_column=Column(JSON))

    # Populated by finalise_session() on completion
    schedule_summary: Optional[str] = None  # one-paragraph overview for list view
    total_duration_minutes: Optional[int] = None
    error_summary: Optional[str] = None  # populated on PARTIAL outcome

    # Full pipeline results — populated by finalise_session() for fast reads
    result_recipes: Optional[list] = Field(default=None, sa_column=Column(JSON))
    result_schedule: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # LLM token usage (observability — no enforcement in V1)
    token_usage: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Celery task tracking (for cancellation)
    celery_task_id: Optional[str] = None

    # Timing
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
