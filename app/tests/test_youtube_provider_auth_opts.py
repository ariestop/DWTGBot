"""YouTube provider forwards cookiefile opt to yt-dlp calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings
from app.domain.entities.media_info import DownloadOption, MediaInfo
from app.domain.enums import MediaKind, Platform
from app.infrastructure.providers.youtube import YouTubeProvider
from app.infrastructure.storage.local_storage import LocalStorage


class _FakeYtDlp:
    def __init__(self) -> None:
        self.extract_calls: list[dict[str, Any]] = []
        self.probe_calls: list[dict[str, Any]] = []
        self.download_calls: list[dict[str, Any]] = []

    async def extract_info(
        self,
        url: str,
        *,
        extra_opts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.extract_calls.append({"url": url, "extra_opts": extra_opts})
        return {
            "id": "yt_1",
            "title": "Short",
            "duration": 12.0,
            "formats": [
                {
                    "height": 720,
                    "vcodec": "avc1.64001f",
                    "acodec": "none",
                    "ext": "mp4",
                    "filesize_approx": 1_000_000,
                }
            ],
        }

    async def probe_size(
        self,
        url: str,
        *,
        format_spec: str,
        extra_opts: dict[str, Any] | None = None,
    ) -> int | None:
        self.probe_calls.append(
            {
                "url": url,
                "format_spec": format_spec,
                "extra_opts": extra_opts,
            }
        )
        return 123

    async def download(
        self,
        url: str,
        *,
        format_spec: str,
        target_dir: Path,
        postprocessors: list[dict[str, Any]] | None = None,
        merge_output_format: str | None = None,
        extra_opts: dict[str, Any] | None = None,
        on_progress: Any = None,
    ) -> list[Path]:
        self.download_calls.append(
            {
                "url": url,
                "format_spec": format_spec,
                "target_dir": target_dir,
                "postprocessors": postprocessors,
                "merge_output_format": merge_output_format,
                "extra_opts": extra_opts,
                "on_progress": on_progress,
            }
        )
        out = target_dir / "out.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return [out]


def _info() -> MediaInfo:
    return MediaInfo(
        platform=Platform.YOUTUBE,
        media_id="yt_1",
        title="Short",
        kind=MediaKind.VIDEO,
        duration_sec=12.0,
        raw={"available_heights": [720], "duration": 12.0},
    )


def _option() -> DownloadOption:
    return DownloadOption(key="video_720", label="Видео 720p", kind=MediaKind.VIDEO, height=720)


def _provider(fake: _FakeYtDlp, settings: Any) -> YouTubeProvider:
    return YouTubeProvider(settings=settings, ytdlp=fake, storage=LocalStorage(settings))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_info_uses_youtube_cookiefile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookie_path = tmp_path / "yt.cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(cookie_path))
    get_settings.cache_clear()
    settings = get_settings()
    fake = _FakeYtDlp()

    await _provider(fake, settings).get_info("https://youtube.com/shorts/yt_1")

    assert fake.extract_calls[0]["extra_opts"] == {"cookiefile": str(cookie_path)}


@pytest.mark.asyncio
async def test_probe_size_uses_youtube_cookiefile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookie_path = tmp_path / "yt.cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(cookie_path))
    get_settings.cache_clear()
    settings = get_settings()
    fake = _FakeYtDlp()

    size = await _provider(fake, settings).probe_size(
        "https://youtube.com/shorts/yt_1", info=_info(), option=_option()
    )

    assert size == 123
    assert fake.probe_calls[0]["extra_opts"] == {"cookiefile": str(cookie_path)}


@pytest.mark.asyncio
async def test_download_uses_youtube_cookiefile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookie_path = tmp_path / "yt.cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(cookie_path))
    get_settings.cache_clear()
    settings = get_settings()
    fake = _FakeYtDlp()

    result = await _provider(fake, settings).download(
        "https://youtube.com/shorts/yt_1",
        _option(),
        target_dir=str(tmp_path / "job_1"),
    )

    assert result.files
    assert fake.download_calls[0]["extra_opts"] == {"cookiefile": str(cookie_path)}


@pytest.mark.asyncio
async def test_cookiefile_missing_falls_back_to_no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", "/tmp/missing-youtube.cookies.txt")
    get_settings.cache_clear()
    settings = get_settings()
    fake = _FakeYtDlp()

    await _provider(fake, settings).get_info("https://youtube.com/shorts/yt_1")

    assert fake.extract_calls[0]["extra_opts"] is None
