"""
core/llm.py
Shared LLM retry decorator for all nodes that call Claude.

Uses tenacity for exponential backoff on transient API errors.
Only retries on errors that are genuinely transient — validation
errors, auth errors, and programming bugs are never retried.
"""

import logging
from functools import wraps
from typing import TypeVar

from anthropic import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Exceptions that indicate transient failures worth retrying.
# These are network/capacity issues, not logic bugs:
#   APITimeoutError: Claude took too long to respond (network or model load)
#   APIConnectionError: TCP-level failure (transient network blip)
#   RateLimitError: API quota hit — backoff lets the bucket refill
#   InternalServerError: Anthropic-side 5xx — usually clears quickly
# NOT included: AuthenticationError, BadRequestError (these are never transient)
RETRYABLE_EXCEPTIONS = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)

# Decorator applied to _invoke_llm() closures in generator, enricher, and renderer.
# Usage: @llm_retry (wraps an async function).
# Policy: up to 3 attempts with exponential backoff (2s → 4s → 8s... capped at 30s).
# reraise=True: after exhausting attempts, the original exception propagates so the
# node's error handler can classify it (timeout vs RAG failure vs unknown).
llm_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
    # Log a WARNING before each sleep so operators can see retry behavior in logs
    # without enabling DEBUG-level noise across the whole application.
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


def is_timeout_error(exc: Exception) -> bool:
    """Check if an exception is a timeout error using proper type checking.

    Used by enricher_node to route LLM_TIMEOUT vs RAG_FAILURE error types.
    Timeouts and other transient errors have the same recoverable=True treatment
    in error_router, but different error_types let the frontend show distinct messages.
    """
    return isinstance(exc, APITimeoutError)


def extract_token_usage(result, node_name: str) -> dict:
    """
    Extract token usage from a LangChain structured output result.

    LangChain Pydantic results don't carry usage metadata directly, but the
    underlying AIMessage (available via with_structured_output with
    include_raw=True) does. This helper safely extracts what's available.

    Two formats tried in priority order:
      1. response_metadata["usage"] — older LangChain convention
      2. usage_metadata — LangChain >=0.2 convention (preferred)

    Returns a dict like:
      {"node": "recipe_generator", "input_tokens": 1234, "output_tokens": 567}

    The token_usage list in GRASPState uses operator.add, so each node's dict
    is appended. finalise_session() sums them for Session.token_usage.
    """
    usage = {"node": node_name}

    # If result has response_metadata (raw AIMessage passthrough)
    if hasattr(result, "response_metadata"):
        meta = result.response_metadata
        if "usage" in meta:
            usage["input_tokens"] = meta["usage"].get("input_tokens", 0)
            usage["output_tokens"] = meta["usage"].get("output_tokens", 0)
            return usage

    # If result carries usage_metadata (LangChain >=0.2 convention)
    if hasattr(result, "usage_metadata") and result.usage_metadata:
        usage["input_tokens"] = getattr(result.usage_metadata, "input_tokens", 0)
        usage["output_tokens"] = getattr(result.usage_metadata, "output_tokens", 0)
        return usage

    # Neither format available — return partial dict with just the node name.
    # This is not an error; some structured output paths don't expose usage.
    return usage
