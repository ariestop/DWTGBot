"""A19: yt-dlp size probe returns known byte counts pre-download."""

from __future__ import annotations

import shutil

import pytest

from app.config import Settings
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner, _extract_known_size


def _make_runner(monkeypatch: pytest.MonkeyPatch) -> YtDlpRunner:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    runner = YtDlpRunner(settings)

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(runner, "_trip_if_open", _noop)
    monkeypatch.setattr(runner, "_note_success", _noop)
    monkeypatch.setattr(runner, "_report_if_throttle", _noop)
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    return runner


def test_extract_known_size_prefers_requested_formats_sum() -> None:
    payload = {
        "requested_formats": [
            {"filesize": 100},
            {"filesize_approx": 40},
        ]
    }

    assert _extract_known_size(payload) == 140


def test_extract_known_size_uses_top_level_fallback() -> None:
    assert _extract_known_size({"filesize_approx": 123}) == 123


@pytest.mark.asyncio
async def test_probe_size_passes_format_and_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _make_runner(monkeypatch)
    captured: dict[str, object] = {}

    def _capture(url: str, opts: dict[str, object]) -> dict[str, object]:
        captured["url"] = url
        captured["opts"] = opts
        return {"requested_formats": [{"filesize": 10}, {"filesize_approx": 20}]}

    monkeypatch.setattr(runner, "_extract_sync", staticmethod(_capture))

    size = await runner.probe_size(
        "https://youtube.com/watch?v=abc",
        format_spec="bestvideo+bestaudio",
    )

    assert size == 30
    opts = captured["opts"]
    assert opts["format"] == "bestvideo+bestaudio"
    assert opts["simulate"] is True
    assert opts["allowed_extractors"] == ["Youtube", "YoutubeTab", "Instagram"]
    assert callable(opts["match_filter"])
