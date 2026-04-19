"""Repository interface for download jobs."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.download_job import DownloadJob


class JobsRepository(ABC):
    """Persistence-agnostic interface for download jobs."""

    @abstractmethod
    async def create(self, job: DownloadJob) -> DownloadJob: ...

    @abstractmethod
    async def get(self, job_id: int) -> DownloadJob | None: ...

    @abstractmethod
    async def update(self, job: DownloadJob) -> DownloadJob: ...

    @abstractmethod
    async def increment_retries(self, job_id: int) -> int: ...

    @abstractmethod
    async def count_active_for_user(self, user_id: int) -> int:
        """Number of in-flight jobs (PENDING or PROCESSING) for ``user_id``.

        Used by the per-user concurrency cap (`MAX_CONCURRENT_JOBS_PER_USER`).
        Implementations should use a single ``SELECT count(*)`` — never a full
        scan — and rely on the index on ``(user_id, status)``.
        """

    @abstractmethod
    async def create_if_under_cap(self, job: DownloadJob, *, cap: int) -> DownloadJob | None:
        """Atomically: if user has < ``cap`` active jobs, insert ``job``.

        Returns the persisted entity (with assigned id) on success, or
        ``None`` when the user is already at/over the cap.

        The previous "count then create" pattern in
        ``EnqueueDownloadUseCase`` has a TOCTOU race that lets two
        concurrent ``/start`` clicks each see ``active == cap-1`` and
        both insert, busting the cap by one (and starving the worker
        pool). Implementations MUST serialise concurrent checks for
        the same ``user_id`` — Postgres advisory locks are the
        recommended primitive here.
        """
