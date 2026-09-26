"""Instagram provider forwards cookiefile opt to yt-dlp calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings
from app.domain.entities.media_info import DownloadOption, MediaInfo, MediaItem
from app.domain.enums import MediaKind, Platform
from app.exceptions import DownloadError, MediaPrivateError
from app.infrastructure.providers.instagram import InstagramProvider
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
            "id": "ig_1",
            "title": "Reel",
            "description": "Caption",
            "ext": "mp4",
            "vcodec": "avc1",
            "duration": 12.0,
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
        merge_output_format: str | None = None,
        extra_opts: dict[str, Any] | None = None,
        force_transcode: bool = False,
        on_progress: Any = None,
    ) -> list[Path]:
        self.download_calls.append(
            {
                "url": url,
                "format_spec": format_spec,
                "target_dir": target_dir,
                "merge_output_format": merge_output_format,
                "extra_opts": extra_opts,
                "force_transcode": force_transcode,
                "on_progress": on_progress,
            }
        )
        out = target_dir / "out.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return [out]


def _info() -> MediaInfo:
    return MediaInfo(
        platform=Platform.INSTAGRAM,
        media_id="ig_1",
        title="Reel",
        kind=MediaKind.VIDEO,
        items=(MediaItem(kind=MediaKind.VIDEO, url="https://instagram.com/reel/ig_1/"),),
        raw={"is_gallery": False, "kinds": ["video"]},
    )


def _option() -> DownloadOption:
    return DownloadOption(key="single_video", label="Скачать видео", kind=MediaKind.VIDEO)


@pytest.mark.asyncio
async def test_get_info_uses_instagram_cookiefile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookie_path = tmp_path / "ig.cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("INSTAGRAM_COOKIES_FILE", str(cookie_path))
    get_settings.cache_clear()
    settings = get_settings()
    fake = _FakeYtDlp()
    provider = InstagramProvider(settings=settings, ytdlp=fake, storage=LocalStorage(settings))  # type: ignore[arg-type]

    await provider.get_info("https://instagram.com/reel/ig_1/")

    assert fake.extract_calls[0]["extra_opts"] == {
        "cookiefile": str(cookie_path),
        "ignore_no_formats_error": True,
    }


@pytest.mark.asyncio
async def test_probe_size_uses_instagram_cookiefile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookie_path = tmp_path / "ig.cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("INSTAGRAM_COOKIES_FILE", str(cookie_path))
    get_settings.cache_clear()
    settings = get_settings()
    fake = _FakeYtDlp()
    provider = InstagramProvider(settings=settings, ytdlp=fake, storage=LocalStorage(settings))  # type: ignore[arg-type]

    size = await provider.probe_size(
        "https://instagram.com/reel/ig_1/", info=_info(), option=_option()
    )

    assert size == 123
    assert fake.probe_calls[0]["extra_opts"]["cookiefile"] == str(cookie_path)


@pytest.mark.asyncio
async def test_download_uses_instagram_cookiefile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookie_path = tmp_path / "ig.cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("INSTAGRAM_COOKIES_FILE", str(cookie_path))
    get_settings.cache_clear()
    settings = get_settings()
    fake = _FakeYtDlp()
    provider = InstagramProvider(settings=settings, ytdlp=fake, storage=LocalStorage(settings))  # type: ignore[arg-type]

    result = await provider.download(
        "https://instagram.com/reel/ig_1/",
        _option(),
        target_dir=str(tmp_path / "job_1"),
    )

    assert result.files
    assert fake.download_calls[0]["extra_opts"] == {"cookiefile": str(cookie_path)}


@pytest.mark.asyncio
async def test_cookiefile_missing_falls_back_to_no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTAGRAM_COOKIES_FILE", "/tmp/missing.cookies.txt")
    get_settings.cache_clear()
    settings = get_settings()
    fake = _FakeYtDlp()
    provider = InstagramProvider(settings=settings, ytdlp=fake, storage=LocalStorage(settings))  # type: ignore[arg-type]

    await provider.get_info("https://instagram.com/reel/ig_1/")

    assert fake.extract_calls[0]["extra_opts"] == {"ignore_no_formats_error": True}


class _RejectingCookiesYtDlp(_FakeYtDlp):
    """Instagram answering a flagged session with 400 (or a stall)."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def extract_info(
        self,
        url: str,
        *,
        extra_opts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if extra_opts and "cookiefile" in extra_opts:
            self.extract_calls.append({"url": url, "extra_opts": extra_opts})
            raise self._error
        return await super().extract_info(url, extra_opts=extra_opts)


def _provider_with_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: _FakeYtDlp
) -> InstagramProvider:
    cookie_path = tmp_path / "ig.cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("INSTAGRAM_COOKIES_FILE", str(cookie_path))
    get_settings.cache_clear()
    settings = get_settings()
    return InstagramProvider(settings=settings, ytdlp=fake, storage=LocalStorage(settings))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_rejected_cookies_fall_back_to_anonymous_and_are_suspended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _RejectingCookiesYtDlp(
        DownloadError("[Instagram] x: Video info extraction failed: HTTP Error 400: Bad Request")
    )
    provider = _provider_with_cookies(tmp_path, monkeypatch, fake)

    info = await provider.get_info("https://instagram.com/reel/ig_1/")
    await provider.get_info("https://instagram.com/reel/ig_1/")

    assert info.media_id == "ig_1"
    assert [call["extra_opts"].get("cookiefile") for call in fake.extract_calls] == [
        str(tmp_path / "ig.cookies.txt"),
        None,
        None,
    ]


@pytest.mark.asyncio
async def test_private_error_with_cookies_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _RejectingCookiesYtDlp(MediaPrivateError("private account"))
    provider = _provider_with_cookies(tmp_path, monkeypatch, fake)

    with pytest.raises(MediaPrivateError):
        await provider.get_info("https://instagram.com/reel/ig_1/")

    assert len(fake.extract_calls) == 1
