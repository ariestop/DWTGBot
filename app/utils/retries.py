"""Shared tenacity retry policies."""

from __future__ import annotations

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.exceptions import DownloadError, ProviderError


def network_retry(attempts: int = 3) -> AsyncRetrying:
    """
    Retry policy for transient network/provider failures.
    Does not retry on validation errors or unsupported platforms.
    """
    return AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ProviderError, DownloadError)),
        reraise=True,
    )
