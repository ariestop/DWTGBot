"""A19: yt-dlp size probe returns known byte counts pre-download."""

from __future__ import annotations

import shutil

import pytest

from app.config import Settings
from app.infrastructure.downloader import ytdlp_runner as runner_module
from app.infrastructure.downloader.ytdlp_results import extract_known_size, selected_media_urls
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner


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


def testextract_known_size_prefers_requested_formats_sum() -> None:
    payload = {
        "requested_formats": [
            {"filesize": 100},
            {"filesize_approx": 40},
        ]
    }

    assert extract_known_size(payload) == 140


def testextract_known_size_uses_top_level_fallback() -> None:
    assert extract_known_size({"filesize_approx": 123}) == 123


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


_V = "https://scontent-iad3-2.cdninstagram.com/v.mp4"
_A = "https://scontent-iad6-1.cdninstagram.com/a.m4a"


def test_selected_media_urls_uses_requested_formats() -> None:
    payload = {"url": None, "requested_formats": [{"url": _V}, {"url": _A}]}

    assert selected_media_urls(payload) == [_V, _A]


def test_selected_media_urls_is_empty_when_an_entry_has_no_url() -> None:
    payload = {"entries": [{"url": _V}, {"webpage_url": "https://instagram.com/p/x/"}]}

    assert selected_media_urls(payload) == []


@pytest.mark.asyncio
async def test_probe_size_falls_back_to_head_of_selected_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Instagram's anonymous DASH formats carry no filesize/duration; the
    CDN still answers HEAD with Content-Length."""
    runner = _make_runner(monkeypatch)
    payload = {
        "requested_formats": [
            {"url": _V, "filesize": None, "tbr": 1372.8},
            {"url": _A, "filesize": None, "tbr": 68.3},
        ]
    }
    monkeypatch.setattr(runner, "_extract_sync", staticmethod(lambda _u, _o: payload))
    sizes = {_V: 5_811_387, _A: 289_965}
    monkeypatch.setattr(runner_module, "head_content_length", lambda url, *, proxy_url: sizes[url])

    size = await runner.probe_size("https://www.instagram.com/reel/x/", format_spec="best")

    assert size == 5_811_387 + 289_965


@pytest.mark.asyncio
async def test_probe_size_head_fallback_unknown_when_any_head_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _make_runner(monkeypatch)
    payload = {"requested_formats": [{"url": _V}, {"url": _A}]}
    monkeypatch.setattr(runner, "_extract_sync", staticmethod(lambda _u, _o: payload))
    monkeypatch.setattr(
        runner_module,
        "head_content_length",
        lambda url, *, proxy_url: 100 if url == _V else None,
    )

    assert await runner.probe_size("https://www.instagram.com/reel/x/", format_spec="b") is None
