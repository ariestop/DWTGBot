"""TempLink entity + service unit tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.application.services.temp_link_service import TempLinkService
from app.config import get_settings
from app.domain.entities.temp_link import TempLink
from app.domain.repositories.temp_links_repo import TempLinksRepository


class TestTempLinkEntity:
    def _link(self, **kwargs: object) -> TempLink:
        defaults: dict = {
            "id": 1,
            "token": "t" * 32,
            "job_id": 42,
            "file_path": "/var/lib/dwtgbot/storage/jobs/42/x.mp4",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "max_downloads": 3,
        }
        defaults.update(kwargs)
        return TempLink(**defaults)  # type: ignore[arg-type]

    def test_usable_when_fresh(self) -> None:
        assert self._link().is_usable() is True

    def test_unusable_when_expired(self) -> None:
        link = self._link(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        assert link.is_usable() is False

    def test_unusable_when_inactive(self) -> None:
        link = self._link(is_active=False)
        assert link.is_usable() is False

    def test_register_use_decrements_remaining_and_deactivates_on_limit(self) -> None:
        link = self._link(max_downloads=2)
        link.register_use()
        assert link.downloads_count == 1
        assert link.is_active is True
        link.register_use()
        assert link.downloads_count == 2
        assert link.is_active is False
        assert link.is_usable() is False


# ---------- service ----------


class _FakeRepo(TempLinksRepository):
    def __init__(self) -> None:
        self.created: list[TempLink] = []

    async def create(self, link: TempLink) -> TempLink:
        link.id = len(self.created) + 1
        self.created.append(link)
        return link

    async def get_by_token(self, token: str):
        for li in self.created:
            if li.token == token:
                return li
        return None

    async def update(self, link: TempLink) -> TempLink:
        return link

    async def deactivate_expired(self) -> int:
        return 0

    async def list_inactive_with_files(self, limit: int = 500):
        return []

    async def try_register_use(self, token: str):
        link = await self.get_by_token(token)
        if link is None or not link.is_usable():
            return None
        link.register_use()
        return link


@pytest.mark.asyncio
async def test_issue_creates_record_and_url() -> None:
    settings = get_settings()
    repo = _FakeRepo()
    svc = TempLinkService(settings=settings, repo=repo)

    link, url = await svc.issue(job_id=7, file_path="/var/lib/dwtgbot/storage/jobs/7/a.mp4")

    assert link.id == 1
    assert len(link.token) >= settings.TEMP_LINK_TOKEN_BYTES
    assert link.max_downloads == settings.TEMP_LINK_MAX_DOWNLOADS
    assert url.startswith(settings.PUBLIC_BASE_URL + "/d/")
    assert url.endswith(link.token)
