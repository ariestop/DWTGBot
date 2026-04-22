"""BRAND_FOOTER is appended to captions (ADR-0010 §2.1 task item 3)."""

from __future__ import annotations

from app.application.services.delivery_service import _caption
from app.domain.entities.media_info import DownloadResult
from app.domain.enums import MediaKind


def _result(title: str = "Demo") -> DownloadResult:
    return DownloadResult(
        files=("/tmp/x.mp4",),
        total_size_bytes=1024,
        primary_mime="video/mp4",
        title=title,
        kind=MediaKind.VIDEO,
    )


def test_caption_without_footer_matches_legacy_shape() -> None:
    caption = _caption(_result(), 1024, footer="")
    assert caption == "<b>Demo</b>\nРазмер файла: 1.0 KB"


def test_caption_with_footer_appends_with_blank_line() -> None:
    # Visible blank line between the metadata and the brand footer —
    # matches the spec in docs/tasks/instant-download-ux.md §3.
    caption = _caption(_result(), 1024, footer="Спасибо за использование нашего бота @dwtgbot")
    assert caption.endswith("\n\nСпасибо за использование нашего бота @dwtgbot")
    assert "<b>Demo</b>" in caption


def test_caption_escapes_html_special_chars_in_footer() -> None:
    caption = _caption(_result(), 1024, footer="thanks <user>&friends")
    assert "thanks &lt;user&gt;&amp;friends" in caption


def test_caption_escapes_html_special_chars_in_title() -> None:
    caption = _caption(_result(title="Me & You <3"), 1024)
    assert "<b>Me &amp; You &lt;3</b>" in caption
