"""Post-download step: make MP4 outputs playable in mobile Telegram.

Mobile Telegram (iOS/Android) decodes through hardware codecs that
require a very narrow subset of formats. Anything else plays on desktop
(ffmpeg-based) but freezes the video track on phones while audio decodes
normally -- the exact "frozen frame, sound works" symptom users reported
for VP9-in-MP4 (common for YouTube 1080p) and HEVC-in-MP4 (sometimes
served by Instagram). Telegram's own file spec
(https://core.telegram.org/api/files#video) lists H.264 + AAC as the
expected pair.

Every failure leaves the original file untouched (log and move on), so a
post-step regression cannot break downloads that used to reach the user.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.logging_config import get_logger

_logger = get_logger(__name__)

# ``-movflags +faststart`` only applies to ISO BMFF containers, and
# Telegram's mobile players expect MP4.
FASTSTART_EXTS = frozenset({".mp4", ".mov", ".m4v"})

_MOBILE_OK_VCODECS = frozenset({"h264", "avc1"})
_MOBILE_OK_ACODECS = frozenset({"aac"})
_MOBILE_OK_PIX_FMTS = frozenset({"yuv420p", "yuvj420p"})

_PROBE_TIMEOUT_S = 10
_REMUX_TIMEOUT_S = 120
_TRANSCODE_TIMEOUT_S = 600

# Fast path: byte-level container rewrite that moves ``moov`` to the head.
# Completes in well under a second for sub-100 MB files and preserves the
# original quality exactly.
_REMUX_ARGS = ("-c", "copy", "-movflags", "+faststart")

# Slow path, tuned for Telegram mobile clients:
#   * high@4.1 is the widest-supported H.264 profile/level combo that
#     covers 1080p30 on every shipping iOS/Android decoder; main@4.0
#     overflows for 1080p60 sources (common on Instagram) and some
#     decoders refuse to start on level-non-conforming streams.
#   * ``-r 30`` caps the frame rate per Telegram's spec ("max 30 fps") and
#     avoids the 60-fps mobile-decoder stall altogether.
#   * 48 kHz / stereo AAC is the pair Telegram's own uploader normalises
#     to; it avoids edge cases with 44.1 kHz or mono tracks on old Androids.
#   * ``-preset veryfast`` keeps encoding close to real time on 2 vCPU at
#     1080p; CRF 23 is libx264's default sweet spot.
# ``-bsf:v dump_extra`` is deliberately absent: the MP4 muxer stores
# SPS/PPS in the ``avcC`` box and rejects ("Error initializing the muxer:
# Invalid argument") streams that also carry them inline.
_TRANSCODE_ARGS = (
    "-c:v",
    "libx264",
    "-preset",
    "veryfast",
    "-profile:v",
    "high",
    "-level",
    "4.1",
    "-pix_fmt",
    "yuv420p",
    "-crf",
    "23",
    "-r",
    "30",
    "-c:a",
    "aac",
    "-b:a",
    "192k",
    "-ar",
    "48000",
    "-ac",
    "2",
    "-movflags",
    "+faststart",
)


@dataclass(frozen=True, slots=True)
class StreamCodecs:
    vcodec: str | None = None
    acodec: str | None = None
    pix_fmt: str | None = None


@dataclass(frozen=True, slots=True)
class FfmpegPlan:
    args: tuple[str, ...]
    timeout_s: int
    event_ok: str
    event_err: str


def needs_transcode(codecs: StreamCodecs, *, force: bool = False) -> bool:
    """Unknown (``None``) fields never force a transcode on their own."""
    return force or (
        (codecs.vcodec is not None and codecs.vcodec not in _MOBILE_OK_VCODECS)
        or (codecs.acodec is not None and codecs.acodec not in _MOBILE_OK_ACODECS)
        or (codecs.pix_fmt is not None and codecs.pix_fmt not in _MOBILE_OK_PIX_FMTS)
    )


def plan_for(*, transcode: bool) -> FfmpegPlan:
    if transcode:
        return FfmpegPlan(
            args=_TRANSCODE_ARGS,
            timeout_s=_TRANSCODE_TIMEOUT_S,
            event_ok="mobile_transcode_done",
            event_err="mobile_transcode_failed",
        )
    return FfmpegPlan(
        args=_REMUX_ARGS,
        timeout_s=_REMUX_TIMEOUT_S,
        event_ok="faststart_remux_done",
        event_err="faststart_remux_failed",
    )


def tmp_output_path(path: Path) -> Path:
    """``video.mp4`` → ``video.remux.tmp.mp4``.

    ffmpeg picks the muxer from the *trailing* extension only; with a
    ``.tmp`` suffix it would pick none (or the wrong one) and
    ``-movflags +faststart`` would fail with "Invalid argument".
    """
    return path.with_name(path.stem + ".remux.tmp" + path.suffix)


def parse_ffprobe_streams(payload: dict[str, object]) -> StreamCodecs:
    vcodec = acodec = pix_fmt = None
    streams = payload.get("streams")
    for stream in streams if isinstance(streams, list) else []:
        if not isinstance(stream, dict):
            continue
        if stream.get("codec_type") == "video" and vcodec is None:
            vcodec = stream.get("codec_name")
            pix_fmt = stream.get("pix_fmt")
        elif stream.get("codec_type") == "audio" and acodec is None:
            acodec = stream.get("codec_name")
    return StreamCodecs(vcodec=vcodec, acodec=acodec, pix_fmt=pix_fmt)


async def probe_codecs(path: Path, ffprobe_bin: str) -> StreamCodecs:
    """Return the first video/audio stream codecs; unknown fields are ``None``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,pix_fmt",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_PROBE_TIMEOUT_S)
        if proc.returncode != 0:
            return StreamCodecs()
        payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except (TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
        _logger.warning("probe_codecs_failed", file=str(path), error=repr(exc))
        return StreamCodecs()
    return parse_ffprobe_streams(payload if isinstance(payload, dict) else {})


async def ensure_mobile_compatible(
    path: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str | None,
    *,
    force_transcode: bool = False,
) -> None:
    """Rewrite ``path`` in place so Telegram mobile clients can play it.

    Without ffprobe we can't tell whether a transcode is needed, so we take
    the cheap remux (still fixes the moov-at-end case for H.264 sources).
    ``force_transcode`` routes into the slow path unconditionally, for
    sources whose codec names look fine but whose container still isn't
    mobile-safe (Instagram's fragmented MP4, High@5.x, unusual GOPs).
    """
    codecs = await probe_codecs(path, ffprobe_bin) if ffprobe_bin else StreamCodecs()
    plan = plan_for(transcode=needs_transcode(codecs, force=force_transcode))
    tmp = tmp_output_path(path)
    log_codecs = {"vcodec": codecs.vcodec, "acodec": codecs.acodec, "pix_fmt": codecs.pix_fmt}
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0",
            *plan.args,
            str(tmp),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=plan.timeout_s)
        if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tail = (stderr or b"").decode("utf-8", errors="replace").strip().splitlines()[-3:]
            _logger.warning(
                plan.event_err,
                file=str(path),
                returncode=proc.returncode,
                stderr=" | ".join(tail),
                **log_codecs,
            )
            tmp.unlink(missing_ok=True)
            return
        tmp.replace(path)
        _logger.info(plan.event_ok, file=str(path), **log_codecs)
    except (TimeoutError, OSError) as exc:
        _logger.warning(plan.event_err, file=str(path), error=repr(exc))
        tmp.unlink(missing_ok=True)


async def make_mobile_compatible(
    files: Iterable[Path],
    ffmpeg_bin: str,
    ffprobe_bin: str | None,
    *,
    force_transcode: bool = False,
) -> None:
    """Apply :func:`ensure_mobile_compatible` to every MP4-family file."""
    for f in files:
        if f.suffix.lower() in FASTSTART_EXTS:
            await ensure_mobile_compatible(
                f, ffmpeg_bin, ffprobe_bin, force_transcode=force_transcode
            )
