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

    # Primary key — also used as the LangGraph thread_id (see celery task).
    # Using the same UUID as both the DB row PK and the checkpoint thread_id
    # keeps session identity stable across the two systems with no mapping table.
    session_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # Foreign key to user_profiles. All queries scope by user_id first so
    # a user can never read another user's sessions.
    user_id: uuid.UUID = Field(foreign_key="user_profiles.user_id", index=True)

    # SessionStatus as a DB-level string column (not enum) for portability.
    # Index for fast status-filter queries (e.g. "show all pending sessions").
    # Written only by POST /run (→ GENERATING) and finalise_session() (→ terminal).
    status: SessionStatus = Field(default=SessionStatus.PENDING, sa_column=Column(String, nullable=False, index=True))

    # DinnerConcept stored as JSON — pure Pydantic, not a separate table.
    # Snapshotted at creation so concept changes in-flight don't corrupt the run.
    # Validated on read via DinnerConcept.model_validate(session.concept_json).
    concept_json: dict = Field(default_factory=dict, sa_column=Column(JSON))

    # Denormalized from NaturalLanguageSchedule on completion.
    # summary: stored for list views — avoids loading the full result_schedule blob.
    # total_duration_minutes: displayed in session cards without deserializing schedule.
    # error_summary: single-sentence user-visible diagnostic for recoverable kickoff
    # failures or partial outcomes. POST /run may set this while leaving the
    # session PENDING so the row stays truthfully retryable.
    schedule_summary: Optional[str] = None
    total_duration_minutes: Optional[int] = None
    error_summary: Optional[str] = None

    # Full pipeline results persisted by finalise_session() for fast detail reads.
    # Storing here avoids hitting the LangGraph checkpoint on every GET /sessions/{id}.
    # Both are JSON-serialized via Pydantic .model_dump(mode="json") for type safety.
    result_recipes: Optional[list] = Field(default=None, sa_column=Column(JSON))
    result_schedule: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Accumulated LLM token counts across all nodes. Observability only — no
    # enforcement in V1. Shape: {total_input_tokens, total_output_tokens, per_node: [...]}
    token_usage: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Set by POST /run so the session can be cancelled mid-flight.
    # Also used to check task health in admin tooling.
    celery_task_id: Optional[str] = None

    # All timestamps stored as UTC naive datetimes (tzinfo stripped) to avoid
    # SQLAlchemy timezone-awareness inconsistencies across Postgres driver versions.
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    started_at: Optional[datetime] = None   # set by POST /run
    completed_at: Optional[datetime] = None # set by finalise_session()
