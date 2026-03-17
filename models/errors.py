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

from models.enums import ErrorType


class NodeError(BaseModel):
    """Accumulates in GRASPState.errors — the one intentional accumulator."""
    node_name: str
    error_type: ErrorType
    recoverable: bool
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    detail: str
    error_type: str | None = None


class NotFoundError(BaseModel):
    detail: str = "Resource not found"


class PipelineError(BaseModel):
    detail: str
    session_id: str
    error_type: ErrorType
