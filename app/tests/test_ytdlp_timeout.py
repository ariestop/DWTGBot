"""Audit fix A10: yt-dlp download is bounded by DOWNLOAD_TIMEOUT_SECONDS.

Without this guard, a hung source would eat the whole arq JOB_TIMEOUT
budget (download + transcode + upload), causing arq to kill the worker
mid-ffmpeg. We verify the timeout translates to ``DownloadTimeoutError``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import Settings
from app.exceptions import DownloadTimeoutError
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner


def _make_runner(monkeypatch: pytest.MonkeyPatch, *, timeout_s: int) -> YtDlpRunner:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    # Settings.DOWNLOAD_TIMEOUT_SECONDS has ge=30 at parse time (sensible
    # production default), but the test needs a sub-second bound to keep
    # the suite fast. Patch after construction.
    object.__setattr__(settings, "DOWNLOAD_TIMEOUT_SECONDS", timeout_s)
    runner = YtDlpRunner(settings)

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(runner, "_trip_if_open", _noop)
    monkeypatch.setattr(runner, "_note_success", _noop)
    monkeypatch.setattr(runner, "_report_if_throttle", _noop)
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    return runner


@pytest.mark.asyncio
async def test_download_raises_timeout_when_over_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _make_runner(monkeypatch, timeout_s=1)

    def _slow_download(url: str, opts: dict) -> None:
        import time

        time.sleep(5)

    monkeypatch.setattr(runner, "_download_sync", staticmethod(_slow_download))

    with pytest.raises(DownloadTimeoutError):
        await runner.download(
            "https://example.com/fake",
            format_spec="best",
            target_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_download_completes_within_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _make_runner(monkeypatch, timeout_s=5)

    def _fast_download(url: str, opts: dict) -> None:
        # Drop a zero-byte file so ``target_dir.iterdir()`` after the
        # wait returns something non-empty (the production call lists
        # freshly-written files).
        (tmp_path / "out.mp4").write_bytes(b"")

    monkeypatch.setattr(runner, "_download_sync", staticmethod(_fast_download))

    files = await runner.download(
        "https://example.com/ok",
        format_spec="best",
        target_dir=tmp_path,
    )
    assert isinstance(files, list)
