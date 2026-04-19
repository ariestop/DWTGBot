"""Unit tests for :mod:`app.domain.reason_class` (ADR-0007 §2.3).

The classifier is on the hot path of every job failure and feeds the
SLO A2/A3 denominator filter (``reason_class != "user_error"``).
A misclassification silently moves user-error noise into the failure
budget — these tests defend against that.
"""

from __future__ import annotations

import pytest

from app.domain.reason_class import ReasonClass, classify_exception
from app.exceptions import (
    AppError,
    DownloadError,
    DownloadTimeoutError,
    FfmpegError,
    FileTooLargeError,
    InvalidUrlError,
    MediaNotFoundError,
    MediaPrivateError,
    ProviderError,
    StorageError,
    TempLinkExhaustedError,
    TempLinkExpiredError,
    TooManyJobsError,
    UnsupportedPlatformError,
)


@pytest.mark.parametrize(
    "exc, expected",
    [
        # User errors — must NOT count against A2/A3 budget.
        (InvalidUrlError("bad url"), ReasonClass.USER_ERROR),
        (UnsupportedPlatformError("not yet"), ReasonClass.USER_ERROR),
        (MediaNotFoundError("404"), ReasonClass.USER_ERROR),
        (MediaPrivateError("private"), ReasonClass.USER_ERROR),
        (TooManyJobsError("cap"), ReasonClass.USER_ERROR),
        (TempLinkExpiredError("ttl"), ReasonClass.USER_ERROR),
        (TempLinkExhaustedError("max"), ReasonClass.USER_ERROR),
        # Provider errors — yt-dlp / ffmpeg / generic provider misery.
        (ProviderError("upstream"), ReasonClass.PROVIDER_ERROR),
        (FfmpegError("transcode"), ReasonClass.PROVIDER_ERROR),
        # Network errors — transient I/O.
        (DownloadTimeoutError("slow"), ReasonClass.NETWORK_ERROR),
        (TimeoutError("socket"), ReasonClass.NETWORK_ERROR),
        (ConnectionError("reset"), ReasonClass.NETWORK_ERROR),
        # Internal — operational bugs.
        (StorageError("disk"), ReasonClass.INTERNAL_ERROR),
        # Fallback: a generic ``DownloadError`` is treated as
        # provider-side rather than internal so a flaky platform
        # doesn't masquerade as a bug in our own code.
        (DownloadError("?"), ReasonClass.PROVIDER_ERROR),
        # FileTooLargeError is a DownloadError subclass — should land
        # in PROVIDER_ERROR via the AppError fallback (the option came
        # from the provider's metadata).
        (FileTooLargeError("big"), ReasonClass.PROVIDER_ERROR),
        # Truly unexpected → INTERNAL.
        (RuntimeError("boom"), ReasonClass.INTERNAL_ERROR),
        (ValueError("oops"), ReasonClass.INTERNAL_ERROR),
    ],
)
def test_classify_exception(exc: BaseException, expected: ReasonClass) -> None:
    assert classify_exception(exc) is expected


def test_user_error_dominates_provider_error_in_subclass_resolution() -> None:
    """``MediaPrivateError`` IS-A ``ProviderError`` but must classify as
    ``user_error`` — the user picked a private piece of content, that
    is not a provider regression. Order in the classifier matters."""
    assert classify_exception(MediaPrivateError("p")) is ReasonClass.USER_ERROR


def test_appbase_exception_falls_to_provider_error() -> None:
    """A bare ``AppError`` is not in any explicit bucket but still
    represents a *handled* failure path. Fallback puts it in
    PROVIDER_ERROR so dashboards stay actionable."""
    assert classify_exception(AppError("?")) is ReasonClass.PROVIDER_ERROR


def test_reason_class_label_values_are_documented() -> None:
    """Sanity check that the enum values match docs/35- §4.1.

    If you add a new value, update §35 §4.1, the SLO denominator in
    §3.1 A2/A3 (which filters on ``reason_class``), and the dashboard
    skeleton in §8 in the same PR (P9).
    """
    assert {c.value for c in ReasonClass} == {
        "ok",
        "user_error",
        "provider_error",
        "network_error",
        "internal_error",
    }
