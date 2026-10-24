"""The download child process: options, progress and outcome lines (audit R16)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from app.infrastructure.downloader import ytdlp_child

_ROOT = Path(__file__).resolve().parents[2]


class _FakeYoutubeDL:
    frames: ClassVar[list[dict[str, Any]]] = []
    fail_with: ClassVar[BaseException | None] = None

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts

    def __enter__(self) -> _FakeYoutubeDL:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def download(self, _urls: list[str]) -> None:
        for frame in self.frames:
            for hook in self.opts["progress_hooks"]:
                hook(frame)
        if self.fail_with is not None:
            raise self.fail_with


def _run(monkeypatch: pytest.MonkeyPatch, **fake: Any) -> list[dict[str, Any]]:
    fake_cls = type("_Fake", (_FakeYoutubeDL,), fake)
    monkeypatch.setattr(ytdlp_child, "YoutubeDL", fake_cls)
    out: list[dict[str, Any]] = []
    ytdlp_child.run({"url": "https://youtu.be/x", "opts": {"format": "best"}}, out.append)
    return out


def _frame(downloaded: int) -> dict[str, Any]:
    return {"status": "downloading", "downloaded_bytes": downloaded, "total_bytes": 1000}


def test_progress_is_thinned_and_ends_with_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = [_frame(n) for n in (0, 1, 2, 3, 6, 10, 500, 501, 1000)]

    out = _run(monkeypatch, frames=frames)

    assert out == [
        {"progress": 0.0},
        {"progress": 0.6},
        {"progress": 50.0},
        {"progress": 100.0},
        {"ok": True},
    ]


def test_ytdlp_error_carries_its_message(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run(monkeypatch, fail_with=YtDlpDownloadError("ERROR: HTTP Error 404: Not Found"))

    assert out == [{"error": "ytdlp", "message": "ERROR: HTTP Error 404: Not Found"}]


def test_other_errors_are_unexpected(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run(monkeypatch, fail_with=KeyError("formats"))

    assert out == [{"error": "unexpected", "message": "KeyError: 'formats'"}]


async def test_stdout_carries_protocol_lines_only() -> None:
    # Anything printed in the child (yt-dlp, ffmpeg, a stray print) goes to stderr.
    code = (
        "import sys\n"
        "from app.infrastructure.downloader import ytdlp_child as c\n"
        "def run(request, emit):\n"
        "    print('noise from yt-dlp')\n"
        "    import os; os.system('echo noise from ffmpeg')\n"
        "    emit({'ok': True})\n"
        "c.run = run\n"
        "sys.exit(c.main())\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=_ROOT,
        env={**os.environ, "PYTHONPATH": str(_ROOT)},
    )
    out, err = await asyncio.wait_for(
        proc.communicate(json.dumps({"url": "u", "opts": {}}).encode()), timeout=30
    )

    assert proc.returncode == 0
    assert out.decode().splitlines() == ['{"ok": true}']
    assert "noise from yt-dlp" in err.decode()
    assert "noise from ffmpeg" in err.decode()
