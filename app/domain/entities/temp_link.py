"""TempLink domain entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class TempLink:
    """
    A token-protected one-shot (or limited-uses) link to a stored file.

    The actual file is served by Nginx via X-Accel-Redirect after FastAPI
    validates the token, expiry and remaining downloads counter.
    """

    id: int | None
    token: str
    job_id: int
    file_path: str
    expires_at: datetime
    max_downloads: int
    downloads_count: int = 0
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_usable(self, *, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        return (
            self.is_active
            and self.expires_at > moment
            and self.downloads_count < self.max_downloads
        )

    def can_resume(self, *, now: datetime | None = None) -> bool:
        """Whether a continuation request (Range / HEAD) may be served.

        Continuations of an already started download do not spend the
        counter, so they stay allowed after the last slot is taken —
        otherwise the final download could never finish or seek.
        """
        moment = now or datetime.now(UTC)
        return self.is_active and self.expires_at > moment and self.downloads_count > 0

    def register_use(self) -> None:
        # ``is_active`` is left alone on exhaustion: it means "not expired
        # and not revoked", and cleanup deletes the file once it flips.
        self.downloads_count += 1
        self.updated_at = datetime.now(UTC)
