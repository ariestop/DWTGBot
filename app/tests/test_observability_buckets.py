"""Unit tests for :mod:`app.domain.observability` bucket helpers.

These bin functions feed Prometheus label values; if their cut-offs
drift, the SLO panels in ``docs/35-`` quietly start displaying
different numbers without anyone noticing. Lock the boundaries down
here.
"""

from __future__ import annotations

import pytest

from app.domain.observability import (
    FileSizeClass,
    HandlerOutcome,
    LatencyBucket,
    TempLinkServeResult,
    file_size_class,
    latency_bucket,
)


@pytest.mark.parametrize(
    "size_bytes, expected",
    [
        (None, FileSizeClass.LT_50MB),
        (0, FileSizeClass.LT_50MB),
        (1, FileSizeClass.LT_50MB),
        (50 * 1024 * 1024 - 1, FileSizeClass.LT_50MB),
        (50 * 1024 * 1024, FileSizeClass.LT_200MB),
        (199 * 1024 * 1024, FileSizeClass.LT_200MB),
        (200 * 1024 * 1024, FileSizeClass.GT_200MB),
        (1024 * 1024 * 1024, FileSizeClass.GT_200MB),
    ],
)
def test_file_size_class_boundaries(size_bytes: int | None, expected: FileSizeClass) -> None:
    assert file_size_class(size_bytes) is expected


@pytest.mark.parametrize(
    "latency_ms, expected",
    [
        (-100.0, LatencyBucket.LT_500MS),  # negative clamped to 0
        (0.0, LatencyBucket.LT_500MS),
        (499.999, LatencyBucket.LT_500MS),
        (500.0, LatencyBucket.LT_1500MS),
        (1499.0, LatencyBucket.LT_1500MS),
        (1500.0, LatencyBucket.LT_5000MS),
        (4999.0, LatencyBucket.LT_5000MS),
        (5000.0, LatencyBucket.GT_5000MS),
        (60_000.0, LatencyBucket.GT_5000MS),
    ],
)
def test_latency_bucket_boundaries(latency_ms: float, expected: LatencyBucket) -> None:
    assert latency_bucket(latency_ms) is expected


def test_handler_outcome_label_values() -> None:
    """Cardinality is bounded: three values x four latency buckets =
    12 series for ``bot_message_handled_total``. Hands off."""
    assert {o.value for o in HandlerOutcome} == {"ok", "user_error", "internal_error"}


def test_temp_link_serve_result_label_values() -> None:
    """ADR-0007 §2.8: semantic results, not raw status codes.

    Renaming any of these is an SLO-coupled change because the A6
    denominator in PromQL filters on ``result != "expired"``.
    """
    assert {r.value for r in TempLinkServeResult} == {
        "ok",
        "not_found",
        "expired",
        "gone",
        "forbidden",
        "error",
    }
