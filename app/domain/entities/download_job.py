"""DownloadJob domain entity (pure, infra-free)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.domain.enums import JobStatus, Platform


@dataclass(slots=True)
class DownloadJob:
    """
    A user-initiated download job. Mirrors the `download_jobs` table.

    The bot creates jobs in PENDING state, the worker transitions them through
    PROCESSING -> DONE / FAILED.
    """

    id: int | None
    user_id: int
    chat_id: int
    source_url: str
    platform: Platform
    media_id: str | None = None
    title: str | None = None
    selected_option_key: str | None = None
    selected_format: str | None = None
    status: JobStatus = JobStatus.PENDING
    file_path: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
    telegram_file_id: str | None = None
    public_url: str | None = None
    error_message: str | None = None
    retries_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    def mark_processing(self) -> None:
        self.status = JobStatus.PROCESSING
        self.updated_at = datetime.now(timezone.utc)

    def mark_done(
        self,
        *,
        file_path: str | None = None,
        file_size: int | None = None,
        mime_type: str | None = None,
        telegram_file_id: str | None = None,
        public_url: str | None = None,
    ) -> None:
        self.status = JobStatus.DONE
        self.file_path = file_path
        self.file_size = file_size
        self.mime_type = mime_type
        self.telegram_file_id = telegram_file_id
        self.public_url = public_url
        now = datetime.now(timezone.utc)
        self.updated_at = now
        self.completed_at = now

    def mark_failed(self, error_message: str) -> None:
        self.status = JobStatus.FAILED
        self.error_message = error_message[:1000]
        now = datetime.now(timezone.utc)
        self.updated_at = now
        self.completed_at = now
