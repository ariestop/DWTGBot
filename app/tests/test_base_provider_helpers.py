"""Shared ``BaseProvider`` helpers used by every concrete provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.enums import MediaKind
from app.exceptions import DownloadError
from app.infrastructure.providers.base import BaseProvider


def test_cookie_opts_none_when_unset() -> None:
    assert BaseProvider._cookie_extra_opts("  ", missing_event="x_cookiefile_missing") is None


def test_cookie_opts_none_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.txt"

    assert BaseProvider._cookie_extra_opts(str(missing), missing_event="x_missing") is None


def test_cookie_opts_point_at_existing_file(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")

    assert BaseProvider._cookie_extra_opts(f" {cookies} ", missing_event="x_missing") == {
        "cookiefile": str(cookies)
    }


@pytest.mark.parametrize(
    ("extra", "auth", "expected"),
    [
        (None, None, None),
        ({"playlist_items": "1"}, None, {"playlist_items": "1"}),
        (None, {"cookiefile": "/c"}, {"cookiefile": "/c"}),
        (
            {"playlist_items": "1", "cookiefile": "/override"},
            {"cookiefile": "/c"},
            {"playlist_items": "1", "cookiefile": "/c"},
        ),
    ],
)
def test_merge_extra_opts(
    extra: dict[str, str] | None, auth: dict[str, str] | None, expected: dict[str, str] | None
) -> None:
    assert BaseProvider._merge_extra_opts(extra, auth) == expected


def test_result_from_files_sums_sizes_and_guesses_mime(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 10)
    thumb = tmp_path / "clip.jpg"
    thumb.write_bytes(b"y" * 5)

    result = BaseProvider._result_from_files(
        [video, thumb],
        title="Clip",
        kind=MediaKind.GALLERY,
        done_event="x_download_done",
        empty_error="no files",
    )

    assert result.files == (str(video), str(thumb))
    assert result.total_size_bytes == 15
    assert result.primary_mime == "video/mp4"
    assert (result.title, result.kind) == ("Clip", MediaKind.GALLERY)


def test_result_from_files_rejects_empty_download() -> None:
    with pytest.raises(DownloadError, match="nothing matched"):
        BaseProvider._result_from_files(
            [],
            title="t",
            kind=MediaKind.VIDEO,
            done_event="x_download_done",
            empty_error="nothing matched",
        )
