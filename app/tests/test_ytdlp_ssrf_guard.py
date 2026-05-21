"""A17: yt-dlp opts include SSRF guards."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from app.config import Settings
from app.exceptions import MediaPrivateError
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner
from app.utils.url import is_allowed_host


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


@pytest.mark.asyncio
async def test_extract_info_passes_allowed_extractors_and_match_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _make_runner(monkeypatch)
    captured: dict[str, Any] = {}

    def _capture(url: str, opts: dict[str, Any]) -> dict[str, Any]:
        captured["url"] = url
        captured["opts"] = opts
        return {"id": "abc"}

    monkeypatch.setattr(runner, "_extract_sync", staticmethod(_capture))

    await runner.extract_info("https://youtube.com/watch?v=abc")

    opts = captured["opts"]
    assert opts["allowed_extractors"] == ["Youtube", "YoutubeTab", "Instagram"]
    assert callable(opts["match_filter"])
    assert opts["match_filter"]({"webpage_url": "https://youtube.com/watch?v=abc"}) is None
    disallowed = opts["match_filter"]({"url": "http://169.254.169.254/latest/meta-data/"})
    assert isinstance(disallowed, str)
    assert "Disallowed host" in disallowed


@pytest.mark.asyncio
async def test_extract_info_merges_extra_opts(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _make_runner(monkeypatch)
    captured: dict[str, Any] = {}

    def _capture(url: str, opts: dict[str, Any]) -> dict[str, Any]:
        captured["url"] = url
        captured["opts"] = opts
        return {"id": "ig"}

    monkeypatch.setattr(runner, "_extract_sync", staticmethod(_capture))

    await runner.extract_info(
        "https://instagram.com/reel/abc",
        extra_opts={"cookiefile": "/tmp/ig.cookies.txt"},
    )

    opts = captured["opts"]
    assert opts["cookiefile"] == "/tmp/ig.cookies.txt"
    assert opts["allowed_extractors"] == ["Youtube", "YoutubeTab", "Instagram"]
    assert callable(opts["match_filter"])


@pytest.mark.asyncio
async def test_download_passes_allowed_extractors_and_match_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _make_runner(monkeypatch)
    captured: dict[str, Any] = {}

    def _capture(url: str, opts: dict[str, Any]) -> None:
        captured["url"] = url
        captured["opts"] = opts
        (tmp_path / "out.mp4").write_bytes(b"x")

    monkeypatch.setattr(runner, "_download_sync", staticmethod(_capture))

    await runner.download(
        "https://instagram.com/reel/abc",
        format_spec="best",
        target_dir=tmp_path,
    )

    opts = captured["opts"]
    assert opts["allowed_extractors"] == ["Youtube", "YoutubeTab", "Instagram"]
    assert callable(opts["match_filter"])
    assert opts["match_filter"]({"url": "https://r1---sn.googlevideo.com/videoplayback"}) is None
    assert opts["match_filter"]({"url": "https://example.com/file.mp4"}) is not None


def test_is_allowed_host_accepts_supported_provider_cdns() -> None:
    assert is_allowed_host("https://youtube.com/watch?v=abc") is True
    assert is_allowed_host("https://r1---sn.googlevideo.com/videoplayback") is True
    assert is_allowed_host("https://i.ytimg.com/vi/abc/hqdefault.jpg") is True
    assert is_allowed_host("https://scontent.cdninstagram.com/v/t50.2886-16/xyz.mp4") is True
    assert is_allowed_host("https://scontent-arn2-1.xx.fbcdn.net/v/t51.2885-15/abc.jpg") is True
    assert is_allowed_host("https://169.254.169.254/latest/meta-data/") is False
    assert is_allowed_host("https://example.com/file.mp4") is False


def test_age_gate_error_is_classified_as_private() -> None:
    exc = YtDlpDownloadError(
        "Sign in to confirm your age. This video may be inappropriate for some users. "
        "Use --cookies-from-browser or --cookies for the authentication."
    )

    classified = YtDlpRunner._classify(exc)

    assert isinstance(classified, MediaPrivateError)
