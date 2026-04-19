"""Temporary download link generation."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.domain.entities.temp_link import TempLink
from app.domain.repositories.temp_links_repo import TempLinksRepository
from app.logging_config import get_logger

_logger = get_logger(__name__)


class TempLinkService:
    def __init__(self, *, settings: Settings, repo: TempLinksRepository) -> None:
        self._settings = settings
        self._repo = repo

    async def issue(self, *, job_id: int, file_path: str) -> tuple[TempLink, str]:
        token = secrets.token_urlsafe(self._settings.TEMP_LINK_TOKEN_BYTES)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._settings.TEMP_LINK_TTL_SECONDS
        )

        link = TempLink(
            id=None,
            token=token,
            job_id=job_id,
            file_path=file_path,
            expires_at=expires_at,
            max_downloads=self._settings.TEMP_LINK_MAX_DOWNLOADS,
        )
        created = await self._repo.create(link)
        url = f"{self._settings.PUBLIC_BASE_URL}/d/{token}"
        _logger.info(
            "temp_link_issued",
            job_id=job_id,
            token=token[:8] + "...",
            expires_at=expires_at.isoformat(),
            max_downloads=self._settings.TEMP_LINK_MAX_DOWNLOADS,
        )
        return created, url
