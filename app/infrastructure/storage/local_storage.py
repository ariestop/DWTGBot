"""
Filesystem-backed media storage.

Single owner of the STORAGE_PATH layout:

    STORAGE_PATH/
        jobs/<job_id>/         # final artefacts to be served / sent
        tmp/<scratch>/         # short-lived workdirs for yt-dlp
        gallery_<job_id>.zip   # packaged galleries

Every path produced or accepted here is normalized through
``ensure_within(STORAGE_PATH, ...)`` to defeat path traversal.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

from app.config import Settings
from app.exceptions import StorageError
from app.logging_config import get_logger
from app.utils.filenames import ensure_within, sanitize_filename

_logger = get_logger(__name__)


class LocalStorage:
    def __init__(self, settings: Settings) -> None:
        self._root = Path(settings.STORAGE_PATH)
        self._tmp_root = Path(settings.STORAGE_TMP_PATH)
        self._max_bytes = settings.max_file_size_bytes
        # S10: backpressure threshold. ``0`` disables the check.
        self._min_free_bytes = settings.STORAGE_MIN_FREE_MB * 1024 * 1024

    def init(self) -> None:
        for p in (self._root, self._tmp_root, self._root / "jobs"):
            p.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def job_dir(self, job_id: int) -> Path:
        target = ensure_within(self._root, Path("jobs") / str(job_id))
        target.mkdir(parents=True, exist_ok=True)
        return target

    def reset_job_dir(self, job_id: int) -> Path:
        target = self.job_dir(job_id)
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def make_tmp_workdir(self, prefix: str = "dl-") -> Path:
        self._tmp_root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=self._tmp_root))

    def remove_path(self, path: Path) -> None:
        try:
            target = (
                ensure_within(self._root, path)
                if self._is_under(self._root, path)
                else (ensure_within(self._tmp_root, path))
            )
        except StorageError:
            _logger.warning("storage_remove_blocked", path=str(path))
            return

        if not target.exists():
            return
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)

    def list_files(self, directory: Path) -> list[Path]:
        d = (
            ensure_within(self._root, directory)
            if self._is_under(self._root, directory)
            else (ensure_within(self._tmp_root, directory))
        )
        if not d.exists() or not d.is_dir():
            return []
        return sorted(p for p in d.iterdir() if p.is_file())

    def total_size(self, paths: Iterable[Path]) -> int:
        return sum(p.stat().st_size for p in paths if p.exists())

    def assert_under_max(self, size_bytes: int) -> None:
        if size_bytes > self._max_bytes:
            limit_mb = self._max_bytes // 1024 // 1024
            raise StorageError(f"Result size {size_bytes} exceeds MAX_FILE_SIZE_MB ({limit_mb} MB)")

    def free_bytes(self) -> int:
        """Best-effort free-space probe on STORAGE_PATH.

        Returns ``-1`` when ``statvfs`` is not available (e.g. Windows
        host outside Docker, exotic FS) — callers must treat ``-1`` as
        "skip the check" rather than "no free space".
        """
        try:
            usage = shutil.disk_usage(self._root)
        except OSError:
            return -1
        return int(usage.free)

    def assert_free_space(self) -> None:
        """S10: refuse to start a new download when disk is nearly full.

        Raises ``StorageError`` so the caller can map it onto a user-
        facing "service is busy" message and the worker can fail-fast
        without dirtying the job dir. No-op when ``STORAGE_MIN_FREE_MB``
        is unset (default).
        """
        if self._min_free_bytes <= 0:
            return
        free = self.free_bytes()
        if free < 0:
            return
        if free < self._min_free_bytes:
            min_mb = self._min_free_bytes // 1024 // 1024
            free_mb = free // 1024 // 1024
            raise StorageError(
                f"Storage almost full: {free_mb} MB free, STORAGE_MIN_FREE_MB={min_mb} MB required"
            )

    def package_zip(self, files: list[Path], *, job_id: int, base_name: str) -> Path:
        if not files:
            raise StorageError("No files to package")
        safe = sanitize_filename(base_name, fallback=f"gallery_{job_id}")
        zip_path = self.job_dir(job_id) / f"{Path(safe).stem}.zip"
        zip_path = ensure_within(self._root, zip_path)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for src in files:
                if not src.exists():
                    continue
                zf.write(src, arcname=src.name)
        return zip_path

    @staticmethod
    def _is_under(base: Path, candidate: Path) -> bool:
        try:
            candidate.resolve().relative_to(base.resolve())
        except (ValueError, OSError):
            return False
        return True
