"""Media metadata abstractions returned by providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import MediaKind, Platform


@dataclass(frozen=True, slots=True)
class DownloadOption:
    """
    A single user-selectable download option.

    `key` is opaque to the bot layer — it is passed back unchanged to the provider
    so the provider can resolve it into concrete yt-dlp / pipeline parameters.
    """

    key: str
    label: str
    kind: MediaKind
    height: int | None = None
    bitrate_kbps: int | None = None
    container: str | None = None
    estimated_size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class MediaItem:
    """Single item inside a gallery (or a standalone media)."""

    kind: MediaKind
    url: str
    width: int | None = None
    height: int | None = None
    duration_sec: float | None = None


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """Provider-agnostic metadata for a URL."""

    platform: Platform
    media_id: str
    title: str
    kind: MediaKind
    duration_sec: float | None = None
    items: tuple[MediaItem, ...] = field(default_factory=tuple)
    thumbnail_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """
    Result of a successful download. `files` are absolute paths inside STORAGE_PATH.
    For galleries there may be multiple files; for a zip-packed gallery — exactly one.
    """

    files: tuple[str, ...]
    total_size_bytes: int
    primary_mime: str
    title: str
    kind: MediaKind
