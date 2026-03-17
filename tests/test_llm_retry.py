"""
tests/test_llm_retry.py
Tests for the LLM retry decorator and timeout detection.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from anthropic import APIConnectionError, APITimeoutError, RateLimitError

from core.llm import is_timeout_error, llm_retry

# ── is_timeout_error ─────────────────────────────────────────────────────────

def test_timeout_error_detected():
    exc = APITimeoutError(request=MagicMock())
    assert is_timeout_error(exc) is True


def test_non_timeout_error_not_detected():
    assert is_timeout_error(ValueError("something")) is False


def test_connection_error_not_timeout():
    exc = APIConnectionError(request=MagicMock())
    assert is_timeout_error(exc) is False


# ── llm_retry decorator ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failure():
    """Retry should succeed when a transient error resolves."""
    mock_fn = AsyncMock(side_effect=[
        RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={"retry-after": "1"}),
            body=None,
        ),
        "success",
    ])

    @llm_retry
    async def call():
        return await mock_fn()

    result = await call()
    assert result == "success"
    assert mock_fn.call_count == 2


@pytest.mark.asyncio
async def test_retry_gives_up_after_max_attempts():
    """Should reraise after 3 failed attempts."""
    exc = APIConnectionError(request=MagicMock())
    mock_fn = AsyncMock(side_effect=exc)

    @llm_retry
    async def call():
        return await mock_fn()

    with pytest.raises(APIConnectionError):
        await call()

    assert mock_fn.call_count == 3


@pytest.mark.asyncio
async def test_no_retry_on_validation_error():
    """Non-retryable errors should fail immediately."""
    mock_fn = AsyncMock(side_effect=ValueError("bad input"))

    @llm_retry
    async def call():
        return await mock_fn()

    with pytest.raises(ValueError, match="bad input"):
        await call()

    assert mock_fn.call_count == 1


@pytest.mark.asyncio
async def test_no_retry_on_auth_error():
    """Auth errors should not be retried."""
    from anthropic import AuthenticationError
    mock_fn = AsyncMock(side_effect=AuthenticationError(
        message="invalid key",
        response=MagicMock(status_code=401, headers={}),
        body=None,
    ))

    @llm_retry
    async def call():
        return await mock_fn()

    with pytest.raises(AuthenticationError):
        await call()

    assert mock_fn.call_count == 1
