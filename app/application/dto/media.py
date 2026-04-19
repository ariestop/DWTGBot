"""DTOs related to media analysis presented to the user."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities.media_info import DownloadOption, MediaInfo


@dataclass(frozen=True, slots=True)
class AnalyzedMedia:
    info: MediaInfo
    options: tuple[DownloadOption, ...]
