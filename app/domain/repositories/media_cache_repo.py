"""Repository interface for cached media metadata."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.enums import Platform


@dataclass(slots=True)
class MediaCacheRecord:
    id: int | None
    source_url: str
    platform: Platform
    media_id: str | None
    title: str | None
    metadata_json: dict[str, Any]
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class MediaCacheRepository(ABC):
    @abstractmethod
    async def get_fresh(self, source_url: str) -> MediaCacheRecord | None: ...

    @abstractmethod
    async def upsert(self, record: MediaCacheRecord) -> MediaCacheRecord: ...

    @abstractmethod
    async def purge_expired(self) -> int: ...
