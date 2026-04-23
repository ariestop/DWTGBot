"""Pre-download size cap (`MAX_FILE_SIZE_MB`) — see docs/37-load-and-capacity.md."""

from __future__ import annotations

import pytest

from app.application.use_cases.process_download import ProcessDownloadUseCase
from app.config import get_settings
from app.domain.entities.media_info import DownloadOption, MediaInfo
from app.domain.enums import MediaKind, Platform
from app.exceptions import FileTooLargeError, SizeUnknownError


def _make_uc() -> ProcessDownloadUseCase:
    """Build a use case with stubs sufficient for `_reject_if_estimate_exceeds_cap`."""
    return ProcessDownloadUseCase(
        jobs_repo=None,  # type: ignore[arg-type]  intentionally unused in this test
        providers=None,  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        delivery=None,  # type: ignore[arg-type]
        sender=None,  # type: ignore[arg-type]
        settings=get_settings(),
    )


class _ProbeProvider:
    async def probe_size(
        self,
        url: str,
        *,
        info: MediaInfo,
        option: DownloadOption,
    ) -> int | None:
        del url, info, option
        return self.next_size


def _make_info() -> MediaInfo:
    return MediaInfo(
        platform=Platform.YOUTUBE,
        media_id="abc",
        title="Title",
        kind=MediaKind.VIDEO,
    )


def test_reject_when_estimate_above_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "100")
    get_settings.cache_clear()
    uc = _make_uc()

    huge = DownloadOption(
        key="video_1080",
        label="1080p",
        kind=MediaKind.VIDEO,
        height=1080,
        estimated_size_bytes=200 * 1024 * 1024,
    )
    with pytest.raises(FileTooLargeError):
        uc._reject_if_estimate_exceeds_cap(huge)


def test_allow_when_estimate_below_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "100")
    get_settings.cache_clear()
    uc = _make_uc()

    small = DownloadOption(
        key="audio_mp3",
        label="MP3",
        kind=MediaKind.AUDIO,
        bitrate_kbps=192,
        estimated_size_bytes=10 * 1024 * 1024,
    )
    uc._reject_if_estimate_exceeds_cap(small)


def test_allow_when_estimate_unknown() -> None:
    """Providers may return ``None`` for the estimate; we must not block."""
    uc = _make_uc()
    unknown = DownloadOption(
        key="audio_mp3",
        label="MP3",
        kind=MediaKind.AUDIO,
        bitrate_kbps=192,
        estimated_size_bytes=None,
    )
    uc._reject_if_estimate_exceeds_cap(unknown)


@pytest.mark.asyncio
async def test_probe_size_is_used_when_estimate_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "100")
    get_settings.cache_clear()
    uc = _make_uc()
    provider = _ProbeProvider()
    provider.next_size = 80 * 1024 * 1024
    option = DownloadOption(
        key="video_720",
        label="720p",
        kind=MediaKind.VIDEO,
        height=720,
        estimated_size_bytes=None,
    )

    size = await uc._resolve_pre_download_size(  # type: ignore[arg-type]
        provider=provider,
        url="https://youtube.com/watch?v=abc",
        info=_make_info(),
        option=option,
    )

    assert size == 80 * 1024 * 1024


@pytest.mark.asyncio
async def test_unknown_video_size_fails_before_download() -> None:
    uc = _make_uc()
    provider = _ProbeProvider()
    provider.next_size = None
    option = DownloadOption(
        key="video_1080",
        label="1080p",
        kind=MediaKind.VIDEO,
        height=1080,
        estimated_size_bytes=None,
    )

    with pytest.raises(SizeUnknownError):
        await uc._resolve_pre_download_size(  # type: ignore[arg-type]
            provider=provider,
            url="https://youtube.com/watch?v=abc",
            info=_make_info(),
            option=option,
        )


@pytest.mark.asyncio
async def test_unknown_audio_size_is_allowed() -> None:
    uc = _make_uc()
    provider = _ProbeProvider()
    provider.next_size = None
    option = DownloadOption(
        key="audio_mp3",
        label="MP3",
        kind=MediaKind.AUDIO,
        bitrate_kbps=192,
        estimated_size_bytes=None,
    )

    size = await uc._resolve_pre_download_size(  # type: ignore[arg-type]
        provider=provider,
        url="https://youtube.com/watch?v=abc",
        info=_make_info(),
        option=option,
    )

    assert size is None
