"""Shared domain enums. Kept tiny and dependency-free."""

from __future__ import annotations

from enum import Enum


class Platform(str, Enum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class MediaKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    PHOTO = "photo"
    GALLERY = "gallery"


class DeliveryMethod(str, Enum):
    """How the result was delivered to the user."""

    TELEGRAM_UPLOAD = "telegram_upload"
    TEMP_LINK = "temp_link"
