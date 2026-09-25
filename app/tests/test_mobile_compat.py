"""Mobile-Telegram post-step: remux vs transcode decision and file handling.

ffprobe / ffmpeg are replaced by a fake ``create_subprocess_exec`` so no
real subprocess runs; the fake ffmpeg writes (or does not write) the tmp
output named by its last argument.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.infrastructure.downloader import mobile_compat
from app.infrastructure.downloader.mobile_compat import (
    StreamCodecs,
    ensure_mobile_compatible,
    make_mobile_compatible,
    needs_transcode,
    parse_ffprobe_streams,
    plan_for,
    tmp_output_path,
)


def _ffprobe_payload(vcodec: str, acodec: str, pix_fmt: str = "yuv420p") -> bytes:
    return json.dumps(
        {
            "streams": [
                {"codec_type": "video", "codec_name": vcodec, "pix_fmt": pix_fmt},
                {"codec_type": "audio", "codec_name": acodec},
            ]
        }
    ).encode()


class _FakeProc:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._out = (stdout, stderr)

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out


@dataclass
class _FakeExec:
    ffprobe_stdout: bytes = b"{}"
    ffmpeg_returncode: int = 0
    ffmpeg_writes_output: bool = True
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def __call__(self, *argv: str, **_: object) -> _FakeProc:
        self.calls.append(argv)
        if argv[0] == "ffprobe":
            return _FakeProc(0, stdout=self.ffprobe_stdout)
        if self.ffmpeg_writes_output:
            Path(argv[-1]).write_bytes(b"rewritten")
        return _FakeProc(self.ffmpeg_returncode, stderr=b"line1\nline2\nboom")

    def ffmpeg_args(self) -> tuple[str, ...]:
        (call,) = [c for c in self.calls if c[0] == "ffmpeg"]
        return call


@pytest.fixture
def fake_exec(monkeypatch: pytest.MonkeyPatch) -> _FakeExec:
    fake = _FakeExec()
    monkeypatch.setattr(mobile_compat.asyncio, "create_subprocess_exec", fake)
    return fake


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"original")
    return path


@pytest.mark.parametrize(
    ("codecs", "force", "expected"),
    [
        (StreamCodecs("h264", "aac", "yuv420p"), False, False),
        (StreamCodecs("avc1", "aac", "yuvj420p"), False, False),
        (StreamCodecs("vp9", "aac", "yuv420p"), False, True),
        (StreamCodecs("h264", "opus", "yuv420p"), False, True),
        (StreamCodecs("h264", "aac", "yuv444p"), False, True),
        (StreamCodecs(), False, False),
        (StreamCodecs("h264", "aac", "yuv420p"), True, True),
    ],
)
def test_needs_transcode(codecs: StreamCodecs, force: bool, expected: bool) -> None:
    assert needs_transcode(codecs, force=force) is expected


def test_plans_differ_in_args_timeout_and_events() -> None:
    remux, transcode = plan_for(transcode=False), plan_for(transcode=True)

    assert remux.args == ("-c", "copy", "-movflags", "+faststart")
    assert (remux.event_ok, remux.event_err) == ("faststart_remux_done", "faststart_remux_failed")
    assert "libx264" in transcode.args
    assert transcode.args[-2:] == ("-movflags", "+faststart")
    assert transcode.timeout_s > remux.timeout_s


def test_tmp_output_keeps_media_extension_last() -> None:
    assert tmp_output_path(Path("/x/video.mp4")) == Path("/x/video.remux.tmp.mp4")


def test_parse_ffprobe_streams_takes_first_video_and_audio() -> None:
    payload = {
        "streams": [
            {"codec_type": "audio", "codec_name": "aac"},
            {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le"},
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "opus"},
        ]
    }

    assert parse_ffprobe_streams(payload) == StreamCodecs("hevc", "aac", "yuv420p10le")


def test_parse_ffprobe_streams_tolerates_garbage() -> None:
    assert parse_ffprobe_streams({"streams": "nope"}) == StreamCodecs()


async def test_mobile_safe_source_is_remuxed_in_place(fake_exec: _FakeExec, video: Path) -> None:
    fake_exec.ffprobe_stdout = _ffprobe_payload("h264", "aac")

    await ensure_mobile_compatible(video, "ffmpeg", "ffprobe")

    assert "copy" in fake_exec.ffmpeg_args()
    assert video.read_bytes() == b"rewritten"
    assert not tmp_output_path(video).exists()


async def test_vp9_source_is_transcoded(fake_exec: _FakeExec, video: Path) -> None:
    fake_exec.ffprobe_stdout = _ffprobe_payload("vp9", "opus")

    await ensure_mobile_compatible(video, "ffmpeg", "ffprobe")

    args = fake_exec.ffmpeg_args()
    assert "libx264" in args
    assert args[-1] == str(tmp_output_path(video))
    assert video.read_bytes() == b"rewritten"


async def test_without_ffprobe_takes_cheap_remux(fake_exec: _FakeExec, video: Path) -> None:
    await ensure_mobile_compatible(video, "ffmpeg", None)

    assert [c[0] for c in fake_exec.calls] == ["ffmpeg"]
    assert "copy" in fake_exec.ffmpeg_args()


async def test_force_transcode_skips_codec_verdict(fake_exec: _FakeExec, video: Path) -> None:
    fake_exec.ffprobe_stdout = _ffprobe_payload("h264", "aac")

    await ensure_mobile_compatible(video, "ffmpeg", "ffprobe", force_transcode=True)

    assert "libx264" in fake_exec.ffmpeg_args()


async def test_ffmpeg_failure_keeps_original_and_removes_tmp(
    fake_exec: _FakeExec, video: Path
) -> None:
    fake_exec.ffmpeg_returncode = 1

    await ensure_mobile_compatible(video, "ffmpeg", None)

    assert video.read_bytes() == b"original"
    assert not tmp_output_path(video).exists()


async def test_empty_ffmpeg_output_keeps_original(fake_exec: _FakeExec, video: Path) -> None:
    fake_exec.ffmpeg_writes_output = False

    await ensure_mobile_compatible(video, "ffmpeg", None)

    assert video.read_bytes() == b"original"


async def test_make_mobile_compatible_only_touches_mp4_family(
    fake_exec: _FakeExec, tmp_path: Path
) -> None:
    files = [tmp_path / name for name in ("a.mp4", "b.MOV", "c.webm", "d.jpg")]
    for f in files:
        f.write_bytes(b"x")

    await make_mobile_compatible(files, "ffmpeg", None)

    touched = {Path(c[c.index("-i") + 1]).name for c in fake_exec.calls}
    assert touched == {"a.mp4", "b.MOV"}
