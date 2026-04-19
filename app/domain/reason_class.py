"""Job-failure classification.

A pure-domain enum + classifier used by both the worker (to label
metrics + logs at job completion) and the bot middleware (to tag
``bot_message_handled`` events). Keeping this in the domain layer lets
unit tests cover the classification table without spinning up the
infrastructure stack — see ``ADR-0007`` §2.3.

The five classes mirror ``docs/35-metrics-and-slo.md`` §4.1 verbatim.
Adding a sixth value is a docs-coupled change: update §35 §4.1, the
PromQL recipes in §6, and the dashboard skeleton in §8 in the same
PR (P9).
"""

from __future__ import annotations

from enum import Enum

from app.exceptions import (
    AppError,
    DownloadError,
    DownloadTimeoutError,
    FfmpegError,
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


class ReasonClass(str, Enum):
    """How a job (or a bot interaction) ended.

    Used as a Prometheus label value (low cardinality, stable spelling)
    *and* as a structured-log field. Any new value must be added to
    ``docs/35-`` §4.1 and to the SLO denominator in §3.1 A2/A3 — those
    queries explicitly filter on ``reason_class``.
    """

    OK = "ok"
    USER_ERROR = "user_error"
    PROVIDER_ERROR = "provider_error"
    NETWORK_ERROR = "network_error"
    INTERNAL_ERROR = "internal_error"


# Failure classes that are the user's responsibility (bad URL, private
# content, exceeding the per-user job cap). These MUST NOT count against
# the A2/A3 SLO budget — see ``docs/35-`` §3.1 footnote.
_USER_ERROR_TYPES: tuple[type[BaseException], ...] = (
    InvalidUrlError,
    UnsupportedPlatformError,
    MediaNotFoundError,
    MediaPrivateError,
    TooManyJobsError,
    TempLinkExpiredError,
    TempLinkExhaustedError,
)

# Failures originating in the upstream provider / yt-dlp / ffmpeg.
# Distinguished from network errors so a yt-dlp regression page can
# fire (``DownloadCompletionFastBurn`` in §35 §6.2) without dragging
# in transient connectivity blips.
_PROVIDER_ERROR_TYPES: tuple[type[BaseException], ...] = (
    ProviderError,  # base class — narrower subclasses are listed above
    FfmpegError,
)

# Transient I/O. ``DownloadTimeoutError`` is treated as network rather
# than provider because the worker cannot tell whether the upstream or
# the local socket stalled — both are retryable in the same way.
_NETWORK_ERROR_TYPES: tuple[type[BaseException], ...] = (
    DownloadTimeoutError,
    TimeoutError,
    ConnectionError,
)

# Storage and "file too large" failures are operational bugs (cleanup
# was too aggressive, capacity caps misaligned) — never the user's fault.
_INTERNAL_ERROR_TYPES: tuple[type[BaseException], ...] = (StorageError,)


def classify_exception(exc: BaseException) -> ReasonClass:
    """Map an exception to a SLO-grade ``ReasonClass``.

    Order matters: more specific subclasses are checked before their
    base classes (e.g. ``MediaPrivateError`` is a ``ProviderError`` but
    must classify as ``user_error`` — the user picked private content,
    not a provider regression). The lookups below are arranged
    accordingly.

    Pure function; no logging side effects.
    """
    if isinstance(exc, _USER_ERROR_TYPES):
        return ReasonClass.USER_ERROR
    if isinstance(exc, _NETWORK_ERROR_TYPES):
        return ReasonClass.NETWORK_ERROR
    if isinstance(exc, _PROVIDER_ERROR_TYPES):
        return ReasonClass.PROVIDER_ERROR
    if isinstance(exc, _INTERNAL_ERROR_TYPES):
        return ReasonClass.INTERNAL_ERROR
    # Generic ``DownloadError`` and any other ``AppError`` we did not
    # explicitly classify above — assume provider-side rather than
    # internal so a flaky platform doesn't masquerade as a bug in our
    # own code. ``InvalidUrlError`` etc. are ``AppError`` too but were
    # already caught by ``_USER_ERROR_TYPES``.
    if isinstance(exc, DownloadError | AppError):
        return ReasonClass.PROVIDER_ERROR
    return ReasonClass.INTERNAL_ERROR
