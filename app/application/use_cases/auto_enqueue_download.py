"""
Auto-enqueue a download the moment a URL lands in the bot — no picker.

This use case replaces the manual ``AnalyzeLink → keyboard → callback
→ EnqueueDownload`` pipeline under the ``INSTANT_DOWNLOAD_ENABLED``
flag. The bot still sends a placeholder message (so the user gets a
preview thumbnail + progress bar immediately), and we bundle the
"choose highest-quality option + persist row + push to the queue +
register the progress side-channel" steps into a single atomic
use case.

Contract
--------
The caller (``app/bot/handlers/links.py``) is responsible for:

* Sending the placeholder message to Telegram (photo or text) and
  providing its ``chat_id`` / ``message_id`` to the use case.
* Running the analyze step *before* calling this use case — we take an
  ``AnalyzedMedia`` directly rather than a raw URL. This keeps the
  use case provider-selection-free and testable without a live
  provider registry; ``AnalyzeLinkUseCase`` remains the single place
  that talks to ``yt-dlp`` for metadata.

The use case calls:

1. ``provider.default_option(info)`` to pick a quality without user
   input (YT: highest ``video_<height>``; IG: single/gallery).
2. ``JobsRepository.create_if_under_cap`` — same atomic path as the
   picker flow, same per-user cap.
3. ``QueueProducer.enqueue_download`` — same arq queue.
4. ``ProgressReporter.start`` — registers the job's placeholder under
   ``progress_meta:{job_id}`` so the bot-side updater can find it.

Failure modes
-------------
* ``TooManyJobsError`` propagates — caller replaces the placeholder
  with an error message (same as the picker flow).
* ``DownloadError`` from ``default_option`` propagates — provider
  could not pick a sensible default (rare; e.g. an audio-only source
  that yt-dlp returned with no streams). The placeholder becomes the
  error.
* Any reporter-side failure is swallowed by the reporter itself
  (ADR-0010 §2.2); we never let progress bookkeeping abort an
  enqueue that already succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.dto.jobs import WorkerJobPayload
from app.application.dto.media import AnalyzedMedia
from app.application.ports.progress_reporter import ProgressReporter
from app.application.services.job_metrics import JobMetrics, NoopJobMetrics
from app.application.services.providers import ProviderRegistry
from app.application.services.queue import QueueProducer
from app.domain.entities.download_job import DownloadJob
from app.domain.entities.media_info import DownloadOption, MediaInfo
from app.domain.enums import Platform
from app.domain.repositories.jobs_repo import JobsRepository
from app.exceptions import TooManyJobsError
from app.logging_config import get_logger

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AutoEnqueueInput:
    user_id: int
    chat_id: int
    analyzed: AnalyzedMedia
    source_url: str
    placeholder_message_id: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class AutoEnqueueResult:
    job_id: int
    selected: DownloadOption
    has_description: bool


class AutoEnqueueDownloadUseCase:
    """Pick default option → create row → enqueue → register progress.

    The class is deliberately constructed from the same primitives that
    ``EnqueueDownloadUseCase`` uses (``JobsRepository``, ``QueueProducer``,
    per-user cap). Two paths on the same machinery keep the feature
    flag genuinely swappable — nothing in the worker / DB / queue
    changes depending on which path enqueued a job.
    """

    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        jobs_repo: JobsRepository,
        queue: QueueProducer,
        progress_reporter: ProgressReporter,
        max_concurrent_per_user: int,
        metrics: JobMetrics | None = None,
    ) -> None:
        if max_concurrent_per_user < 1:
            raise ValueError("max_concurrent_per_user must be >= 1")
        self._providers = providers
        self._jobs = jobs_repo
        self._queue = queue
        self._reporter = progress_reporter
        self._max_per_user = max_concurrent_per_user
        self._metrics: JobMetrics = metrics if metrics is not None else NoopJobMetrics()

    async def execute(self, payload: AutoEnqueueInput) -> AutoEnqueueResult:
        info: MediaInfo = payload.analyzed.info
        provider = self._providers.get(info.platform)
        # ``default_option`` was introduced in PR 2; it is the contract
        # point that keeps the picker removable without losing
        # correctness (YT picks highest video_<height>, IG picks
        # single/gallery_all by kind).
        selected = provider.default_option(info)

        job = DownloadJob(
            id=None,
            user_id=payload.user_id,
            chat_id=payload.chat_id,
            source_url=payload.source_url,
            platform=info.platform,
            selected_option_key=selected.key,
        )
        created = await self._jobs.create_if_under_cap(job, cap=self._max_per_user)
        if created is None:
            _logger.info(
                "auto_enqueue_rejected_per_user_cap",
                user_id=payload.user_id,
                cap=self._max_per_user,
            )
            raise TooManyJobsError(
                f"user {payload.user_id} is at the per-user cap ({self._max_per_user})"
            )
        assert created.id is not None  # repo contract

        await self._queue.enqueue_download(
            WorkerJobPayload(job_id=created.id, correlation_id=payload.correlation_id)
        )
        self._metrics.inc_job_created(platform=info.platform)

        # Best-effort: reporter swallows its own Redis errors. We still
        # try/except because ADR-0010 forbids progress failures from
        # breaking the main flow — a broken reporter must never
        # un-enqueue a job we already pushed.
        try:
            await self._reporter.start(
                job_id=created.id,
                chat_id=payload.chat_id,
                message_id=payload.placeholder_message_id,
                thumbnail_url=info.thumbnail_url,
            )
        except Exception:  # pragma: no cover  defensive
            _logger.exception("auto_enqueue_progress_start_failed", job_id=created.id)

        _logger.info(
            "job_auto_enqueued",
            job_id=created.id,
            platform=_platform_str(info.platform),
            option=selected.key,
            user_id=payload.user_id,
        )
        return AutoEnqueueResult(
            job_id=created.id,
            selected=selected,
            has_description=bool(info.description and info.description.strip()),
        )


def _platform_str(platform: Platform) -> str:
    return platform.value
