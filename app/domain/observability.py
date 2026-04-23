"""Observability vocabulary shared across processes.

Tiny domain module that turns *raw numbers* into the *bounded label
values* used by the Prometheus exporter (``ADR-0007``) and by the
structured logs (``docs/14-`` §5). Keeping the binning logic here
prevents two subtly-different definitions of ``lt_200mb`` showing up
in the bot vs the worker.

Adding a new bucket is an SLO-coupled change: update ``docs/35-``
§3.1 (the A4/A5 SLI definition references the exact bucket names) and
the metric description in ``docs/35-`` §7 in the same PR (P9).
"""

from __future__ import annotations

from enum import Enum

# Telegram's own upload cap is 50 MB on the bot API; ``200 MB`` is the
# operational point at which "we definitely had to use a temp link"
# (matches docs/35- §3.1 A4 vs A5). Crossing 200 MB also tends to push
# users into noticeable wait latency, which is why the SLO splits there.
_BOUNDARY_50MB = 50 * 1024 * 1024
_BOUNDARY_200MB = 200 * 1024 * 1024


class FileSizeClass(str, Enum):
    """Coarse bins used as a Prometheus label.

    Keep this set small — it multiplies cardinality on every metric
    that uses it (``job_duration_seconds``, future bandwidth metrics).
    Three bins is the minimum needed to distinguish the SLO bands in
    ``docs/35-`` §3.1.
    """

    LT_50MB = "lt_50mb"
    LT_200MB = "lt_200mb"
    GT_200MB = "gt_200mb"


def file_size_class(size_bytes: int | None) -> FileSizeClass:
    """Classify a file size into one of three SLO buckets.

    ``None`` is treated as "small" rather than as a separate bucket
    because the only path that produces ``None`` today is a worker
    that bailed before computing the size — the worker should still
    log a non-zero observation in the ``lt_50mb`` bucket so dashboards
    don't develop a hole.
    """
    if size_bytes is None or size_bytes < _BOUNDARY_50MB:
        return FileSizeClass.LT_50MB
    if size_bytes < _BOUNDARY_200MB:
        return FileSizeClass.LT_200MB
    return FileSizeClass.GT_200MB


# Latency buckets for the ``bot_message_handled`` event. The cut-offs
# match docs/35- §3.1 A1 (1.5 s is the SLO boundary) plus a "really
# slow" bucket so a 30-s freeze stands out from a 2-s blip on the
# dashboard. Same change-coupling rule as ``FileSizeClass``.
_LATENCY_BOUNDARY_FAST_MS = 500
_LATENCY_BOUNDARY_OK_MS = 1500
_LATENCY_BOUNDARY_SLOW_MS = 5000


class LatencyBucket(str, Enum):
    LT_500MS = "lt_500ms"
    LT_1500MS = "lt_1500ms"
    LT_5000MS = "lt_5000ms"
    GT_5000MS = "gt_5000ms"


def latency_bucket(latency_ms: float) -> LatencyBucket:
    """Bin a handler latency in milliseconds.

    Values < 0 are clamped to 0 — happens only when monotonic clocks
    misbehave under heavy GC pressure; we'd rather record "fast" than
    crash the middleware.
    """
    ms = max(0.0, latency_ms)
    if ms < _LATENCY_BOUNDARY_FAST_MS:
        return LatencyBucket.LT_500MS
    if ms < _LATENCY_BOUNDARY_OK_MS:
        return LatencyBucket.LT_1500MS
    if ms < _LATENCY_BOUNDARY_SLOW_MS:
        return LatencyBucket.LT_5000MS
    return LatencyBucket.GT_5000MS


class HandlerOutcome(str, Enum):
    """How a bot update finished, from the user's perspective.

    Three values keeps cardinality bounded and lets us write the A1
    SLO ratio without extra filtering. ``user_error`` covers updates
    we successfully *handled* but where the user input was the cause
    (bad URL, denied by rate-limit) — counted as "served" for A1
    because the bot stayed responsive.
    """

    OK = "ok"
    USER_ERROR = "user_error"
    INTERNAL_ERROR = "internal_error"


# Temp-link serve outcomes — the ``result`` label on
# ``temp_link_serves_total``. See ``ADR-0007`` §2.8 for why we use
# semantic names instead of the raw HTTP status code.
class TempLinkServeResult(str, Enum):
    OK = "ok"  # 200, file streamed
    NOT_FOUND = "not_found"  # 404 — token unknown (possible enumeration)
    EXPIRED = "expired"  # 410 — past TTL or downloads exhausted
    GONE = "gone"  # 410 — file vanished (cleanup race)
    FORBIDDEN = "forbidden"  # 403 — ensure_within rejected the path
    ERROR = "error"  # 5xx — bug


class ReplayIgnoreReason(str, Enum):
    """Why a worker replay was ignored instead of re-processing the job."""

    DONE = "done"
    FAILED = "failed"
    PROCESSING = "processing"
