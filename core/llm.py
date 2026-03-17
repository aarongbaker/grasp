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

# Exceptions that indicate transient failures worth retrying
RETRYABLE_EXCEPTIONS = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)

llm_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


def is_timeout_error(exc: Exception) -> bool:
    """Check if an exception is a timeout error using proper type checking."""
    return isinstance(exc, APITimeoutError)


def extract_token_usage(result, node_name: str) -> dict:
    """
    Extract token usage from a LangChain structured output result.

    LangChain Pydantic results don't carry usage metadata directly, but the
    underlying AIMessage (available via with_structured_output with
    include_raw=True) does. This helper safely extracts what's available.

    Returns a dict like:
      {"node": "recipe_generator", "input_tokens": 1234, "output_tokens": 567}
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

    return usage
