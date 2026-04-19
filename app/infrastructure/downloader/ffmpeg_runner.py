"""
Async wrapper around ffmpeg subprocess calls.

Most ffmpeg work is done by yt-dlp's postprocessors automatically. This
runner is used for explicit operations the application owns: e.g. converting
a downloaded source to MP3 with a specific bitrate, or basic remuxing.

All arguments are passed via argv (no shell), so we are immune to shell
injection through filenames.
"""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

from app.config import Settings
from app.exceptions import FfmpegError
from app.logging_config import get_logger

_logger = get_logger(__name__)


class FfmpegRunner:
    def __init__(self, settings: Settings) -> None:
        self._bin = settings.FFMPEG_BIN
        self._timeout = settings.DOWNLOAD_TIMEOUT_SECONDS

    async def run(self, args: list[str]) -> None:
        cmd = [self._bin, "-hide_banner", "-loglevel", "error", "-y", *args]
        _logger.debug("ffmpeg_run", cmd=shlex.join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise FfmpegError(f"ffmpeg timed out after {self._timeout}s") from exc

        if proc.returncode != 0:
            tail = (stderr or b"").decode("utf-8", errors="replace").strip().splitlines()[-5:]
            raise FfmpegError("ffmpeg failed: " + " | ".join(tail))

    async def to_mp3(self, src: Path, dst: Path, *, bitrate_kbps: int = 192) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        await self.run(
            [
                "-i",
                str(src),
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                f"{bitrate_kbps}k",
                str(dst),
            ]
        )
        if not dst.exists() or dst.stat().st_size == 0:
            raise FfmpegError("ffmpeg produced empty output")
        return dst
