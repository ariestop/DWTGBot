"""DTOs that flow between bot, services and queue."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import Platform


@dataclass(frozen=True, slots=True)
class EnqueueDownloadInput:
    user_id: int
    chat_id: int
    source_url: str
    platform: Platform
    selected_option_key: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class EnqueueDownloadResult:
    job_id: int


@dataclass(frozen=True, slots=True)
class WorkerJobPayload:
    """Serialized payload pushed onto the queue."""

    job_id: int
    correlation_id: str
