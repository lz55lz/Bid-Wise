"""Async retry utilities with exponential backoff."""
import logging
from collections.abc import Callable
from typing import TypeVar

import tenacity
from tenacity import (
    before_sleep_log,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


def async_retry_with_backoff(
    max_attempts: int = 3,
    min_wait: float = 2.0,
    max_wait: float = 10.0,
    reraise: bool = True,
):
    """Decorator for async functions with exponential backoff retry.

    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries (seconds)
        max_wait: Maximum wait time between retries (seconds)
        reraise: Whether to reraise the final exception after all retries fail
    """
    return tenacity.retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        before_sleep=before_sleep_log(logger, logging.WARNING, exc_info=True),
        reraise=reraise,
        retry_error_callback=lambda retry_state: None,  # For sync fallback
    )


def async_retry_predicate(
    predicate: Callable[[Exception], bool],
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 8.0,
):
    """Retry only on specific exception types.

    Args:
        predicate: Function that returns True if should retry the exception
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries
        max_wait: Maximum wait time between retries
    """
    return tenacity.retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=tenacity.retry_if_exception(predicate),
        before_sleep=before_sleep_log(logger, logging.WARNING, exc_info=True),
        reraise=True,
    )


# Retry on httpx HTTP errors (5xx, timeouts, connection errors)
def is_retryable_http_error(exc: Exception) -> bool:
    """Check if an httpx exception is retryable."""
    import httpx

    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.RemoteProtocolError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # Retry on 5xx server errors
        return 500 <= exc.response.status_code < 600
    return False
