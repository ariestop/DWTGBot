"""Repository interface for temporary download links."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.temp_link import TempLink


class TempLinksRepository(ABC):
    @abstractmethod
    async def create(self, link: TempLink) -> TempLink: ...

    @abstractmethod
    async def get_by_token(self, token: str) -> TempLink | None: ...

    @abstractmethod
    async def update(self, link: TempLink) -> TempLink: ...

    @abstractmethod
    async def deactivate_expired(self) -> int:
        """Mark expired links as inactive. Returns number of rows touched."""

    @abstractmethod
    async def list_inactive_with_files(self, limit: int = 500) -> list[TempLink]:
        """Return inactive/expired links that may still have files on disk."""

    @abstractmethod
    async def try_register_use(self, token: str) -> TempLink | None:
        """Atomically: if the link is usable, increment counter and return it.

        Replaces the previous "load → mutate → update" pattern in the
        ``/d/{token}`` handler, which let two parallel requests on the
        same single-use link both observe ``downloads_count < max`` and
        both succeed (audit fix #5). Implementations MUST do this in a
        single SQL ``UPDATE ... WHERE ... RETURNING`` so the database
        serialises the increment for us.

        Returns the *post-update* entity on success, or ``None`` when
        the link is missing / inactive / expired / exhausted.
        """
