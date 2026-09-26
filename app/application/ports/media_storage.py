"""MediaStorage port — the slice of result storage the application needs.

Implemented by ``app.infrastructure.storage.local_storage.LocalStorage``;
path-traversal protection stays inside the implementation, so callers
only ever receive paths already confined to ``STORAGE_PATH``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class MediaStorage(Protocol):
    def job_dir(self, job_id: int) -> Path:
        """Return (creating if needed) the per-job artefact directory."""

    def reset_job_dir(self, job_id: int) -> Path:
        """Empty the per-job directory (left over from a failed attempt) and return it."""

    def assert_free_space(self) -> None:
        """Raise ``StorageError`` when free disk is below ``STORAGE_MIN_FREE_MB``."""

    def assert_under_max(self, size_bytes: int) -> None:
        """Raise ``StorageError`` when a result exceeds ``MAX_FILE_SIZE_MB``."""

    def package_zip(self, files: list[Path], *, job_id: int, base_name: str) -> Path:
        """Pack ``files`` into a ZIP inside the job directory and return its path."""
