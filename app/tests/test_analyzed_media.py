"""``AnalyzedMedia.source_url``: the URL both enqueue paths persist."""

from __future__ import annotations

from typing import Any

import pytest

from app.application.dto.media import AnalyzedMedia
from app.domain.entities.media_info import MediaInfo
from app.domain.enums import MediaKind, Platform


def _analyzed(raw: dict[str, Any]) -> AnalyzedMedia:
    info = MediaInfo(
        platform=Platform.INSTAGRAM,
        media_id="abc",
        title="t",
        kind=MediaKind.VIDEO,
        raw=raw,
    )
    return AnalyzedMedia(info=info, options=())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            {"source_url": "https://a.example/1", "webpage_url": "https://b.example/2"},
            "https://a.example/1",
        ),
        ({"source_url": "", "webpage_url": "https://b.example/2"}, "https://b.example/2"),
        ({"original_url": "https://c.example/3"}, "https://c.example/3"),
        ({"source_url": None, "webpage_url": 42}, ""),
        ({}, ""),
    ],
)
def test_source_url_prefers_source_then_webpage_then_original(
    raw: dict[str, Any], expected: str
) -> None:
    assert _analyzed(raw).source_url == expected
