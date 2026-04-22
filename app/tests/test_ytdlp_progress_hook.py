"""Tests for the yt-dlp progress_hooks adapter."""

from __future__ import annotations

from app.infrastructure.downloader.ytdlp_runner import (
    _extract_percent,
    _make_progress_hook,
)


def test_percent_str_preferred() -> None:
    assert _extract_percent({"status": "downloading", "_percent_str": " 42.5%"}) == 42.5


def test_percent_str_with_ansi_colors() -> None:
    # yt-dlp can wrap _percent_str in ANSI colour codes (rare, but
    # observed with --color). We explicitly don't strip ANSI here;
    # falling back to byte counters keeps things simple.
    raw = {
        "status": "downloading",
        "_percent_str": "\x1b[0;94m 73.8%\x1b[0m",
        "downloaded_bytes": 738,
        "total_bytes": 1000,
    }
    # ANSI makes the string non-float; fallback to bytes → 73.8.
    assert _extract_percent(raw) == 73.8


def test_percent_from_total_bytes_exact() -> None:
    d = {"status": "downloading", "downloaded_bytes": 25, "total_bytes": 100}
    assert _extract_percent(d) == 25.0


def test_percent_from_total_bytes_estimate() -> None:
    d = {"status": "downloading", "downloaded_bytes": 40, "total_bytes_estimate": 200}
    assert _extract_percent(d) == 20.0


def test_percent_total_zero_returns_none() -> None:
    d = {"status": "downloading", "downloaded_bytes": 10, "total_bytes": 0}
    assert _extract_percent(d) is None


def test_percent_missing_keys_returns_none() -> None:
    assert _extract_percent({"status": "downloading"}) is None


def test_percent_clamped_on_overshoot() -> None:
    # yt-dlp sometimes reports >100% briefly during fragment switching.
    d = {"status": "downloading", "downloaded_bytes": 110, "total_bytes": 100}
    assert _extract_percent(d) == 100.0


def test_hook_ignores_non_downloading_status() -> None:
    received: list[float] = []
    hook = _make_progress_hook(received.append)
    hook({"status": "finished", "_percent_str": "100.0%"})
    assert received == []


def test_hook_forwards_valid_percent() -> None:
    received: list[float] = []
    hook = _make_progress_hook(received.append)
    hook({"status": "downloading", "_percent_str": " 50.0%"})
    assert received == [50.0]


def test_hook_skips_when_no_percent() -> None:
    received: list[float] = []
    hook = _make_progress_hook(received.append)
    hook({"status": "downloading"})
    assert received == []


def test_hook_swallows_callback_exceptions() -> None:
    def _boom(_percent: float) -> None:
        raise RuntimeError("should not propagate")

    hook = _make_progress_hook(_boom)
    # Must not raise — yt-dlp has no recovery path for hook failures.
    hook({"status": "downloading", "_percent_str": "10.0%"})


def test_hook_handles_bogus_percent_string() -> None:
    received: list[float] = []
    hook = _make_progress_hook(received.append)
    hook({"status": "downloading", "_percent_str": "N/A"})
    # Falls through to byte math; no bytes → silent skip.
    assert received == []
