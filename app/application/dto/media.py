"""DTOs related to media analysis presented to the user."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities.media_info import DownloadOption, MediaInfo


@dataclass(frozen=True, slots=True)
class AnalyzedMedia:
    info: MediaInfo
    options: tuple[DownloadOption, ...]

    @property
    def source_url(self) -> str:
        """The URL a download job is created for.

        Providers put the analysed URL into ``MediaInfo.raw``; older or
        cached entries may only carry yt-dlp's own keys. ``""`` when
        none is present.
        """
        raw = self.info.raw or {}
        for key in ("source_url", "webpage_url", "original_url"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value
        return ""
