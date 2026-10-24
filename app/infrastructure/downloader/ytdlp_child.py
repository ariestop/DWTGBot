"""One yt-dlp download in its own process (audit R16, ADR-0014).

    python -m app.infrastructure.downloader.ytdlp_child

:class:`YtDlpRunner` starts this module in a new process group, writes
one JSON request to stdin (``{"url": ..., "opts": {...}}``) and reads
JSON lines from stdout:

* ``{"progress": 42.0}`` — percent, at most every half percent;
* ``{"ok": true}`` — yt-dlp finished;
* ``{"error": "ytdlp", "message": ...}`` — yt-dlp's ``DownloadError``;
  the runner classifies the message as before;
* ``{"error": "unexpected", "message": ...}`` — anything else.

A timeout or a cancelled job kills the whole group, ffmpeg included,
which a thread could not do. The request carries JSON only: the source
guards (``match_filter`` is a function) and the progress hook are added
here. Everything yt-dlp and ffmpeg print goes to stderr; fd 1 is moved
away first so nothing else can write into the protocol stream.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any, TextIO

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from app.infrastructure.downloader import ytdlp_opts

Emit = Callable[[dict[str, Any]], None]

# Smallest change worth a line on the pipe; the bot debounces further.
PROGRESS_STEP = 0.5


def build_opts(opts: dict[str, Any], emit: Emit) -> dict[str, Any]:
    """The request's options plus what cannot cross the pipe."""
    built = dict(opts)
    ytdlp_opts.apply_source_guards(built)
    last: list[float] = []

    def _on_progress(percent: float) -> None:
        if last and percent < 100.0 and abs(percent - last[0]) < PROGRESS_STEP:
            return
        last[:] = [percent]
        emit({"progress": round(percent, 1)})

    built["progress_hooks"] = [ytdlp_opts.make_progress_hook(_on_progress)]
    return built


def run(request: dict[str, Any], emit: Emit) -> None:
    """Download ``request["url"]`` and report the outcome through ``emit``."""
    try:
        with YoutubeDL(build_opts(request["opts"], emit)) as ydl:
            ydl.download([request["url"]])
    except YtDlpDownloadError as exc:
        emit({"error": "ytdlp", "message": str(exc)})
        return
    except Exception as exc:
        emit({"error": "unexpected", "message": f"{type(exc).__name__}: {exc}"})
        return
    emit({"ok": True})


def _emitter(out: TextIO) -> Emit:
    def _emit(message: dict[str, Any]) -> None:
        try:
            out.write(json.dumps(message) + "\n")
            out.flush()
        except (BrokenPipeError, OSError):
            # The worker is gone: nobody will deliver this file.
            os._exit(1)

    return _emit


def main() -> int:
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    run(json.load(sys.stdin), _emitter(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
