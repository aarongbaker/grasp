"""
models/errors.py
NodeError is the structured error model that flows through GRASPState.
ErrorResponse/NotFoundError/PipelineError are FastAPI response models.

Key design: NodeError.recoverable drives whether error_router halts the
pipeline (fatal) or continues (recoverable). error_type enables context-
specific frontend messaging without string parsing.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import ErrorType


class NodeError(BaseModel):
    """Accumulates in GRASPState.errors — the one intentional accumulator.

    Every node that encounters a failure appends a NodeError to state.errors
    via operator.add. Nodes never READ errors (that's error_router's job).
    Separation of concerns: nodes write, router reads, frontend displays.
    """

    # Which node produced this error — used by error_router to attribute blame
    # and by the renderer to build human-readable error summaries.
    node_name: str

    # Typed error category — lets the frontend show context-specific messages
    # without parsing the human-readable `message` string.
    error_type: ErrorType

    # The routing decision key. True = error_router says "continue"; pipeline
    # drops the failed recipe and proceeds with survivors. False = "fatal";
    # pipeline routes to handle_fatal_error and halts.
    recoverable: bool

    # Human-readable description for logs and UI. Not parsed programmatically
    # (use error_type for that). May contain exception text for debugging.
    message: str

    # Unstructured context bag — recipe_name, exception_type, etc.
    # Used by frontend to construct detailed error displays without further
    # parsing the message string. Always include recipe_name if relevant.
    metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Standard FastAPI error response shape. Returned on 4xx/5xx."""
    detail: str
    # Optional structured type for frontend error handling; None = generic error
    error_type: str | None = None


class NotFoundError(BaseModel):
    """Returned on 404 responses. detail is a user-facing message."""
    detail: str = "Resource not found"


class PipelineError(BaseModel):
    """Structured pipeline failure payload returned by session status endpoints.

    Surfaced when session.status == FAILED and the frontend needs to show
    more than just a status string. Combines the FastAPI detail convention
    with GRASP-specific session context.
    """
    detail: str
    session_id: str  # UUID string of the failed session for UI navigation
    error_type: ErrorType  # lets frontend route to specific error UI components
