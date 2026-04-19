"""Filename sanitization + path-traversal protection."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.exceptions import StorageError
from app.utils.filenames import MAX_NAME_LEN, ensure_within, sanitize_filename


class TestSanitize:
    def test_strips_unsafe_chars(self) -> None:
        assert "/" not in sanitize_filename("a/b/c.mp4")
        assert ":" not in sanitize_filename("ab:cd.mp4")

    def test_preserves_extension(self) -> None:
        out = sanitize_filename("Привет, мир!.mp4")
        assert out.endswith(".mp4")

    def test_truncates(self) -> None:
        long = "x" * 500 + ".mp4"
        assert len(sanitize_filename(long)) <= MAX_NAME_LEN

    def test_empty_falls_back(self) -> None:
        assert sanitize_filename("") == "media"
        assert sanitize_filename(None or "") == "media"  # type: ignore[truthy-bool]

    def test_strips_control_chars(self) -> None:
        out = sanitize_filename("ab\x00cd\x07.mp4")
        assert "\x00" not in out and "\x07" not in out

    def test_collapses_multiple_dots(self) -> None:
        assert ".." not in sanitize_filename("a..b...c.mp4")


class TestEnsureWithin:
    def test_allows_relative_inside(self, tmp_path: Path) -> None:
        target = ensure_within(tmp_path, Path("subdir/file.bin"))
        assert str(target).startswith(str(tmp_path.resolve()))

    def test_blocks_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(StorageError):
            ensure_within(tmp_path, Path("../../etc/passwd"))

    def test_blocks_absolute_outside(self, tmp_path: Path) -> None:
        with pytest.raises(StorageError):
            ensure_within(tmp_path, Path("/etc/passwd"))
