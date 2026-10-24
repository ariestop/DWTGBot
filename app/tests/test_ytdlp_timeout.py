"""yt-dlp downloads run in a child process that can be killed (A10, audit R16).

``DOWNLOAD_TIMEOUT_SECONDS`` bounds the download independently of arq's
``JOB_TIMEOUT_SECONDS``. On timeout, and when arq cancels the job, the
runner kills the child's process group: yt-dlp and the ffmpeg it starts.
The child here is a fake script speaking the ``ytdlp_child`` protocol.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.exceptions import (
    DownloadError,
    DownloadTimeoutError,
    MediaNotFoundError,
    UpstreamUnavailableError,
)
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner


class _Breaker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.successes: list[str] = []

    async def is_open(self, _host: str) -> bool:
        return False

    async def record_failure(self, host: str) -> None:
        self.failures.append(host)

    async def record_success(self, host: str) -> None:
        self.successes.append(host)


def _runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    child_body: str,
    *,
    timeout_s: int = 5,
    breaker: _Breaker | None = None,
) -> YtDlpRunner:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    # Settings.DOWNLOAD_TIMEOUT_SECONDS has ge=30 at parse time (sensible
    # production default), but the test needs a short bound to keep the
    # suite fast. Patch after construction.
    object.__setattr__(settings, "DOWNLOAD_TIMEOUT_SECONDS", timeout_s)
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    script = tmp_path / "fake_child.py"
    header = "import json, os, subprocess, sys, time\nrequest = json.load(sys.stdin)\n"
    script.write_text(header + textwrap.dedent(child_body))
    return YtDlpRunner(
        settings,
        breaker=breaker,  # type: ignore[arg-type]
        child_argv=(sys.executable, str(script)),
    )


def _gone(pid: int) -> bool:
    """Dead or a zombie waiting for its (new) parent to reap it."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError, IndexError):
        # ProcessLookupError (ESRCH): the process went away between open and read.
        return True
    return state in ("Z", "X")


async def _wait_gone(pid: int) -> bool:
    for _ in range(50):
        if _gone(pid):
            return True
        await asyncio.sleep(0.05)
    return False


_HANG_WITH_GRANDCHILD = """
    grandchild = subprocess.Popen(["sleep", "60"])
    with open(os.environ["PIDS_FILE"], "w") as f:
        f.write(f"{os.getpid()} {grandchild.pid}")
    print(json.dumps({"progress": 10.0}), flush=True)
    time.sleep(60)
"""


def _pids(path: Path) -> list[int]:
    for _ in range(100):
        if path.exists() and path.read_text().strip():
            return [int(p) for p in path.read_text().split()]
        time.sleep(0.05)
    raise AssertionError("fake child did not start")


@pytest.mark.asyncio
async def test_timeout_kills_the_child_and_its_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pids_file = tmp_path / "pids"
    monkeypatch.setenv("PIDS_FILE", str(pids_file))
    runner = _runner(monkeypatch, tmp_path, _HANG_WITH_GRANDCHILD, timeout_s=1)
    seen: list[float] = []

    started = time.monotonic()
    with pytest.raises(DownloadTimeoutError):
        await runner.download(
            "https://example.com/slow",
            format_spec="best",
            target_dir=tmp_path / "out",
            on_progress=seen.append,
        )

    assert time.monotonic() - started < 5
    assert seen == [10.0]
    for pid in _pids(pids_file):
        assert await _wait_gone(pid), f"pid {pid} survived the timeout"


@pytest.mark.asyncio
async def test_cancelled_job_kills_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # arq cancels the task on JOB_TIMEOUT_SECONDS or shutdown.
    pids_file = tmp_path / "pids"
    monkeypatch.setenv("PIDS_FILE", str(pids_file))
    runner = _runner(monkeypatch, tmp_path, _HANG_WITH_GRANDCHILD, timeout_s=60)

    task = asyncio.create_task(
        runner.download("https://example.com/slow", format_spec="best", target_dir=tmp_path)
    )
    pids = await asyncio.to_thread(_pids, pids_file)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for pid in pids:
        assert await _wait_gone(pid), f"pid {pid} survived the cancel"


@pytest.mark.asyncio
async def test_download_returns_files_and_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    breaker = _Breaker()
    runner = _runner(
        monkeypatch,
        tmp_path,
        """
        target = os.path.dirname(request["opts"]["outtmpl"])
        print("not json: stray output", flush=True)
        for pct in (12.5, 50.0, 100.0):
            print(json.dumps({"progress": pct}), flush=True)
        open(os.path.join(target, "out.mp4"), "wb").write(b"x")
        print(json.dumps({"ok": True}), flush=True)
        """,
        breaker=breaker,
    )
    seen: list[float] = []

    files = await runner.download(
        "https://www.youtube.com/watch?v=abc",
        format_spec="best",
        target_dir=tmp_path / "out",
        on_progress=seen.append,
    )

    assert [f.name for f in files] == ["out.mp4"]
    assert seen == [12.5, 50.0, 100.0]
    assert breaker.successes == ["youtube.com"]


@pytest.mark.asyncio
async def test_ytdlp_errors_are_classified_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    breaker = _Breaker()
    runner = _runner(
        monkeypatch,
        tmp_path,
        """
        print(json.dumps({"error": "ytdlp", "message": request["url"].rsplit("/", 1)[1]}))
        """,
        breaker=breaker,
    )

    with pytest.raises(MediaNotFoundError):
        await runner.download(
            "https://x.test/HTTP Error 404: Not Found", format_spec="b", target_dir=tmp_path
        )
    with pytest.raises(UpstreamUnavailableError):
        await runner.download(
            "https://x.test/HTTP Error 429: Too Many Requests", format_spec="b", target_dir=tmp_path
        )

    assert breaker.failures == ["x.test"]


@pytest.mark.parametrize(
    "child_body",
    [
        "sys.exit(3)",
        'print(json.dumps({"error": "unexpected", "message": "KeyError: x"}))',
    ],
)
@pytest.mark.asyncio
async def test_child_failure_is_a_download_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child_body: str
) -> None:
    runner = _runner(monkeypatch, tmp_path, child_body)

    with pytest.raises(DownloadError) as info:
        await runner.download("https://example.com/v", format_spec="best", target_dir=tmp_path)

    assert type(info.value) is DownloadError


@pytest.mark.asyncio
async def test_request_carries_json_options_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = tmp_path / "request.json"
    monkeypatch.setenv("REQUEST_DUMP", str(dump))
    runner = _runner(
        monkeypatch,
        tmp_path,
        """
        open(os.environ["REQUEST_DUMP"], "w").write(json.dumps(request))
        print(json.dumps({"ok": True}))
        """,
    )
    object.__setattr__(runner._settings, "HTTPS_PROXY_URL", "http://proxy.test:3128")

    await runner.download(
        "https://www.instagram.com/reel/abc/",
        format_spec="best",
        target_dir=tmp_path,
        merge_output_format="mp4",
        extra_opts={"cookiefile": "/tmp/ig.cookies.txt"},
    )

    request: dict[str, Any] = json.loads(dump.read_text())
    assert request["url"] == "https://www.instagram.com/reel/abc/"
    opts = request["opts"]
    assert opts["cookiefile"] == "/tmp/ig.cookies.txt"
    assert opts["proxy"] == "http://proxy.test:3128"
    assert opts["merge_output_format"] == "mp4"
    # Added by the child: functions cannot cross the pipe.
    assert "match_filter" not in opts
    assert "progress_hooks" not in opts


@pytest.mark.asyncio
async def test_real_child_module_speaks_the_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real module end to end: a host outside the allowed extractors
    # fails inside yt-dlp before any network call.
    runner = _runner(monkeypatch, tmp_path, "")
    runner._child_argv = (sys.executable, "-m", "app.infrastructure.downloader.ytdlp_child")
    object.__setattr__(runner._settings, "HTTPS_PROXY_URL", "")
    env_proxy = {k: v for k, v in os.environ.items() if "proxy" in k.lower()}
    for key in env_proxy:
        monkeypatch.delenv(key)

    with pytest.raises(DownloadError) as info:
        await runner.download(
            "https://example.com/video.mp4", format_spec="best", target_dir=tmp_path
        )

    assert "no suitable extractor" in str(info.value).lower()
