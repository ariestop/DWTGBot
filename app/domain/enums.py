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


class ProgressStage(str, Enum):
    """
    Coarse-grained download lifecycle stages surfaced to the user.

    The worker emits these via ``ProgressReporter`` at phase boundaries;
    the bot translates them into human-readable progress captions.
    Terminal stages (``DONE``, ``FAILED``, ``CANCELLED``) cause the
    progress updater to stop editing and clean up the placeholder
    message — see ADR-0010 §2.2.
    """

    ANALYZING = "analyzing"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    UPLOADING = "uploading"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """True for stages after which no further updates are expected."""
        return self in (ProgressStage.DONE, ProgressStage.CANCELLED, ProgressStage.FAILED)
