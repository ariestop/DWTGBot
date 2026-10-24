"""Worker-side orchestration of a single download job."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.application.ports.media_sender import MediaSender
from app.application.ports.media_storage import MediaStorage
from app.application.ports.progress_reporter import NoopProgressReporter, ProgressReporter
from app.application.services.delivery_ledger import DeliveredRecord, DeliveryLedger
from app.application.services.delivery_service import DeliveryService
from app.application.services.job_cancellation import JobCancellationStore
from app.application.services.job_metrics import JobMetrics, NoopJobMetrics
from app.application.services.media_cache_codec import info_from_cache, is_stale_youtube_cache
from app.application.services.providers import Provider, ProviderRegistry
from app.config import Settings
from app.domain.entities.download_job import DownloadJob
from app.domain.entities.media_info import DownloadOption, DownloadResult, MediaInfo
from app.domain.enums import JobStatus, MediaKind, ProgressStage
from app.domain.observability import ReplayIgnoreReason, file_size_class
from app.domain.reason_class import ReasonClass, classify_exception
from app.domain.repositories.jobs_repo import JobsRepository
from app.domain.repositories.media_cache_repo import MediaCacheRepository
from app.exceptions import (
    AppError,
    FileTooLargeError,
    JobCancelledError,
    JobConcurrentUpdateError,
    RecipientUnavailableError,
    SizeUnknownError,
    StorageError,
)
from app.logging_config import get_logger
from app.utils.correlation import bind_context

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProcessDownloadInput:
    job_id: int
    correlation_id: str
    attempt: int = 1


class ProcessDownloadUseCase:
    def __init__(
        self,
        *,
        jobs_repo: JobsRepository,
        providers: ProviderRegistry,
        storage: MediaStorage,
        delivery: DeliveryService,
        sender: MediaSender,
        settings: Settings,
        metrics: JobMetrics | None = None,
        progress_reporter: ProgressReporter | None = None,
        cancellation: JobCancellationStore | None = None,
        media_cache: MediaCacheRepository | None = None,
        delivery_ledger: DeliveryLedger | None = None,
    ) -> None:
        self._jobs = jobs_repo
        self._providers = providers
        self._storage = storage
        self._delivery = delivery
        self._sender = sender
        self._settings = settings
        # Default to Noop so unit tests that construct the use case
        # without metrics keep passing. ``composition.build_worker``
        # always wires a real sink.
        self._metrics: JobMetrics = metrics if metrics is not None else NoopJobMetrics()
        # ADR-0010 §2.2: reporter is an optional side channel. Default
        # Noop keeps legacy picker tests insulated from progress
        # infrastructure; production wiring injects RedisProgressReporter.
        self._reporter: ProgressReporter = (
            progress_reporter if progress_reporter is not None else NoopProgressReporter()
        )
        # ADR-0010 §2.1: cooperative cancellation. ``None`` means "no
        # cancel plumbing" — every ``_check_cancel`` call becomes a
        # no-op, the worker keeps running the classic flow. Production
        # wires a ``RedisJobCancellationStore``.
        self._cancellation = cancellation
        self._media_cache = media_cache
        # Audit R2: a retry after a successful delivery must not send the
        # file again. ``None`` (unit tests) disables the check.
        self._ledger = delivery_ledger

    async def execute(self, payload: ProcessDownloadInput) -> None:
        job = await self._jobs.get(payload.job_id)
        if job is None:
            _logger.error("job_not_found", job_id=payload.job_id)
            return

        with bind_context(
            job_id=job.id,
            user_id=job.user_id,
            chat_id=job.chat_id,
            request_id=payload.correlation_id,
            platform=job.platform.value,
        ):
            await self._run(job, attempt=payload.attempt)

    async def _run(self, job: DownloadJob, *, attempt: int = 1) -> None:
        # Monotonic clock — immune to wall-clock jumps from NTP. Used
        # for ``job_duration_seconds`` (ADR-0007 §2.1, A4/A5 SLO).
        started_monotonic = time.monotonic()

        if attempt > 1 and job.status is JobStatus.PROCESSING:
            # arq retry of a transient failure: the row stayed PROCESSING
            # on purpose (see docs/09-queue-and-workers.md §8).
            _logger.info("job_retry_attempt", attempt=attempt)
        else:
            if self._guard_replay(job):
                return
            transitioned = await self._transition_to(job, JobStatus.PROCESSING, ReasonClass.OK)
            if transitioned is None:
                return
            job = transitioned

        try:
            if attempt > 1 and await self._finish_if_delivered(
                job, started_monotonic=started_monotonic
            ):
                return
            await self._download_and_deliver(job, started_monotonic=started_monotonic)
        except JobCancelledError:
            # Cooperative cancel — user-initiated, non-retryable. The
            # bot already emitted ``reporter.cancel`` when the callback
            # fired, so the placeholder UI is already correct; here we
            # only reconcile the DB row and clear the flag so a
            # reused job_id (shouldn't happen, but belt + braces) does
            # not pick up a stale cancel.
            _logger.info("job_cancelled_by_user", job_id=job.id)
            await self._handle_cancellation(job)
            return
        except AppError as exc:
            reason = classify_exception(exc)
            if exc.is_retryable:
                # Re-raise so arq increments ``job_try`` and re-schedules
                # with backoff. The job row stays in ``PROCESSING`` —
                # cleaner than flapping back to PENDING — and the
                # task-level wrapper marks it FAILED only when arq
                # exhausts ``max_tries`` (see infrastructure/queue/tasks.py).
                #
                # Intentionally NOT calling reporter.fail here: the job
                # is not terminally failed yet. mark_terminally_failed
                # is the single terminal sink (called by the arq task
                # wrapper) and it emits reporter.fail exactly once.
                _logger.warning(
                    "job_failed_retryable",
                    error=str(exc),
                    reason_class=reason.value,
                )
                raise
            _logger.warning("job_failed_permanent", error=str(exc), reason_class=reason.value)
            await self._fail(job, exc.user_message, str(exc), reason)
        except Exception as exc:
            # Unknown exception class — treat as retryable (transient by
            # default). If it is in fact permanent, the last-attempt
            # path in ``mark_terminally_failed`` will record FAILED.
            reason = classify_exception(exc)
            _logger.exception("job_failed_unexpected", error=str(exc), reason_class=reason.value)
            raise

    async def _download_and_deliver(self, job: DownloadJob, *, started_monotonic: float) -> None:
        """Happy path of a PROCESSING job; every failure propagates to ``_run``.

        Progress boundaries (ADR-0010 §2.2): ANALYZING 5% → DOWNLOADING
        0→70% (yt-dlp hook) → PROCESSING 70% → UPLOADING 95% → finish.
        """
        self._assert_free_space()

        # Phase 1 — ANALYZING. Cancel check before any network I/O: the
        # user may have tapped cancel while the job sat on the queue.
        await self._emit_progress(job.id, percent=5.0, stage=ProgressStage.ANALYZING)
        await self._check_cancel(job.id)
        provider, info, selected = await self._analyze(job)

        # Phase 2 — DOWNLOADING. The boundary write moves the UI even for
        # sources whose yt-dlp run emits no hook frames (rare). Second
        # cancel check avoids a wasted yt-dlp session.
        await self._emit_progress(job.id, percent=0.0, stage=ProgressStage.DOWNLOADING)
        await self._check_cancel(job.id)
        result = await self._download(job, provider=provider, info=info, option=selected)

        # Phase 3 — PROCESSING (mobile-compat transcode already ran inside
        # the provider; ffmpeg progress is not observable cheaply), then
        # Phase 4 — UPLOADING (Telegram send or temp-link issue).
        await self._emit_progress(job.id, percent=70.0, stage=ProgressStage.PROCESSING)
        await self._emit_progress(job.id, percent=95.0, stage=ProgressStage.UPLOADING)
        outcome = await self._delivery.deliver(
            job_id=job.id or 0,
            chat_id=job.chat_id,
            result=result,
        )
        delivered = DeliveredRecord(
            method=outcome.method,
            public_url=outcome.public_url,
            primary_telegram_file_id=outcome.primary_telegram_file_id,
            delivered_path=outcome.delivered_path,
            file_size=outcome.file_size,
            mime_type=result.primary_mime,
            title=info.title,
            media_id=info.media_id,
            selected_format=selected.container or selected.label,
        )
        if self._ledger is not None and job.id is not None:
            await self._ledger.record(job_id=job.id, delivered=delivered)
        await self._complete(job, delivered, started_monotonic=started_monotonic)

    async def _finish_if_delivered(self, job: DownloadJob, *, started_monotonic: float) -> bool:
        """Retry of a job that already reached the user: only write DONE."""
        if self._ledger is None or job.id is None:
            return False
        delivered = await self._ledger.get(job_id=job.id)
        if delivered is None:
            return False
        _logger.info("job_already_delivered", method=delivered.method.value)
        await self._complete(job, delivered, started_monotonic=started_monotonic)
        return True

    async def _complete(
        self, job: DownloadJob, delivered: DeliveredRecord, *, started_monotonic: float
    ) -> None:
        job.title = delivered.title
        job.media_id = delivered.media_id
        job.selected_format = delivered.selected_format
        job.mark_done(
            file_path=delivered.delivered_path,
            file_size=delivered.file_size,
            mime_type=delivered.mime_type,
            telegram_file_id=delivered.primary_telegram_file_id,
            public_url=delivered.public_url,
        )
        job = await self._persist_done(job)
        await self._finish_progress(job.id)
        self._record_done(delivered, started_monotonic=started_monotonic)

    def _assert_free_space(self) -> None:
        """S10 (audit fix): backpressure on a near-full disk.

        Runs *after* the PROCESSING transition (so the orphan reaper sees
        the row) but *before* allocating the job dir, keeping the disk
        clean. Fail-permanent: retrying under the same disk pressure would
        just thrash. ``storage`` may be ``None`` in unit tests that only
        exercise the retry/error machinery (test_retry_semantics).
        """
        if self._storage is None:
            return
        try:
            self._storage.assert_free_space()
        except StorageError as exc:
            raise StorageError(
                str(exc),
                user_message=(
                    "Сервис временно перегружен (нет свободного места). "
                    "Попробуйте через несколько минут."
                ),
            ) from exc

    async def _analyze(self, job: DownloadJob) -> tuple[Provider, MediaInfo, DownloadOption]:
        provider = self._providers.get(job.platform)
        info = await self._cached_info(job) or await provider.get_info(job.source_url)
        selected = self._pick_option(provider.build_options(info), job.selected_option_key)
        if selected is None:
            raise AppError(
                f"Selected option not available anymore: {job.selected_option_key}",
                user_message="Выбранный вариант больше недоступен. Пришлите ссылку заново.",
            )
        estimate_bytes = await self._resolve_pre_download_size(
            provider=provider,
            url=job.source_url,
            info=info,
            option=selected,
        )
        self._reject_if_estimate_exceeds_cap(selected, estimate_bytes=estimate_bytes)
        return provider, info, selected

    async def _cached_info(self, job: DownloadJob) -> MediaInfo | None:
        """The analysis the bot cached for this URL, if still fresh.

        The bot analysed the link moments ago; asking the platform again
        doubles the metadata requests per link, and Instagram answers
        bursts from one IP by stalling connections until they time out.
        Cache errors fall back to the provider.
        """
        if self._media_cache is None:
            return None
        try:
            record = await self._media_cache.get_fresh(job.source_url)
        except Exception:  # pragma: no cover  best-effort cache read
            _logger.exception("media_cache_read_failed")
            return None
        if record is None:
            return None
        info = info_from_cache(record, platform=job.platform)
        if is_stale_youtube_cache(info):
            return None
        _logger.info("job_info_from_cache", platform=job.platform.value)
        return info

    async def _download(
        self,
        job: DownloadJob,
        *,
        provider: Provider,
        info: MediaInfo,
        option: DownloadOption,
    ) -> DownloadResult:
        target = self._storage.reset_job_dir(job.id or 0)
        result = await provider.download(
            job.source_url,
            option,
            target_dir=str(target),
            on_progress=self._scaled_download_hook(job.id, provider=provider),
            info=info,
        )
        # Providers fall back to ``out_dir.name`` (= the numeric job id)
        # when they have no better title — that would surface the
        # internal queue id as the caption title.
        if info.title:
            result = dataclasses.replace(result, title=info.title)
        return result

    async def _finish_progress(self, job_id: int | None) -> None:
        """Terminal success: the progress updater clears the placeholder.

        The reporter swallows Redis errors (ADR-0010), so this call
        cannot mask a completed download.
        """
        if job_id is None:
            return
        try:
            await self._reporter.finish(job_id=job_id)
        except Exception:  # pragma: no cover  defensive
            _logger.exception("progress_finish_failed", job_id=job_id)

    def _record_done(self, outcome: DeliveredRecord, *, started_monotonic: float) -> None:
        # Duration first so the Histogram + the Counter land on the same
        # scrape; then job_done and the status change stay paired (P11).
        duration_seconds = time.monotonic() - started_monotonic
        size_class = file_size_class(outcome.file_size)
        self._metrics.observe_job_duration(file_size_class=size_class, seconds=duration_seconds)
        _logger.info(
            "job_done",
            method=outcome.method.value,
            size=outcome.file_size,
            total_seconds=round(duration_seconds, 3),
            file_size_class=size_class.value,
        )
        self._emit_transition(JobStatus.PROCESSING, JobStatus.DONE, ReasonClass.OK)

    def _emit_transition(self, previous: JobStatus, to: JobStatus, reason: ReasonClass) -> None:
        """Metric + ``job_status_changed`` for a transition Postgres accepted.

        Every status change ends here, after the write, so a Prometheus
        scrape never counts a transition the database rejected.
        """
        self._metrics.inc_status_change(to=to, reason_class=reason)
        _logger.info(
            "job_status_changed",
            from_=previous.value,
            to=to.value,
            reason_class=reason.value,
        )

    def _guard_replay(self, job: DownloadJob) -> bool:
        if job.status is JobStatus.PENDING:
            return False
        reason_map = {
            JobStatus.DONE: ReplayIgnoreReason.DONE,
            JobStatus.FAILED: ReplayIgnoreReason.FAILED,
            JobStatus.PROCESSING: ReplayIgnoreReason.PROCESSING,
        }
        reason = reason_map.get(job.status)
        if reason is None:  # pragma: no cover  defensive
            return False
        self._metrics.inc_replay_ignored(reason=reason)
        _logger.warning(
            "job_replay_ignored",
            job_id=job.id,
            current_status=job.status.value,
            reason=reason.value,
        )
        return True

    async def _resolve_pre_download_size(
        self,
        *,
        provider: Provider,
        url: str,
        info: MediaInfo,
        option: DownloadOption,
    ) -> int | None:
        estimate = option.estimated_size_bytes
        if estimate is not None:
            return estimate
        size_bytes = await provider.probe_size(url, info=info, option=option)
        if size_bytes is not None:
            _logger.info("job_size_probed", option=option.key, size_bytes=size_bytes)
            return size_bytes
        if option.kind is MediaKind.VIDEO:
            _logger.warning("job_size_unknown", option=option.key)
            raise SizeUnknownError(
                f"Unable to determine size for option {option.key}",
                user_message=(
                    "Не удалось заранее определить размер файла. "
                    "Попробуйте другую ссылку или качество поменьше."
                ),
            )
        return None

    def _reject_if_estimate_exceeds_cap(
        self,
        option: DownloadOption,
        *,
        estimate_bytes: int | None = None,
    ) -> None:
        """Fail fast (P10) when the provider's own estimate already exceeds
        ``MAX_FILE_SIZE_MB``. The post-download check in ``LocalStorage`` and
        ``DeliveryService`` is still authoritative; this just avoids burning
        bandwidth/disk on a job we are guaranteed to refuse."""
        estimate = option.estimated_size_bytes if estimate_bytes is None else estimate_bytes
        cap = self._settings.max_file_size_bytes
        if estimate is not None and estimate > cap:
            _logger.warning(
                "job_rejected_estimate_over_cap",
                option=option.key,
                estimate_bytes=estimate,
                cap_bytes=cap,
            )
            raise FileTooLargeError(
                f"Estimated size {estimate} > MAX_FILE_SIZE_MB ({cap // 1024 // 1024} MB)",
                user_message=(
                    "Этот вариант слишком большой "
                    f"(оценка ~{estimate // 1024 // 1024} МБ, лимит "
                    f"{cap // 1024 // 1024} МБ). Выберите качество поменьше."
                ),
            )

    @staticmethod
    def _pick_option(options: list[DownloadOption], key: str | None) -> DownloadOption | None:
        if not key:
            return None
        for opt in options:
            if opt.key == key:
                return opt
        return None

    async def _transition_to(
        self, job: DownloadJob, target: JobStatus, reason: ReasonClass
    ) -> DownloadJob | None:
        """PENDING → PROCESSING. A conflict with a replayed task (the row
        moved on meanwhile) ends the run quietly via ``_guard_replay``."""
        previous = job.status
        if target is JobStatus.PROCESSING:
            job.mark_processing()
        else:  # pragma: no cover  defensive
            raise ValueError(f"_transition_to does not handle {target}")
        try:
            job = await self._jobs.update(job)
        except JobConcurrentUpdateError:
            latest = await self._jobs.get(job.id or 0)
            if latest is None:
                _logger.warning("job_update_conflict_job_missing", job_id=job.id)
                return None
            if self._guard_replay(latest):
                return None
            raise
        self._emit_transition(previous, target, reason)
        return job

    async def mark_terminally_failed(self, job_id: int, exc: BaseException) -> None:
        """Public entrypoint for the arq task wrapper.

        Called once after arq has exhausted ``max_tries`` for retryable
        / unexpected exceptions. ``_run`` no longer touches the job row
        in those branches (it re-raises so arq can re-schedule), so
        without this call the row would forever remain ``PROCESSING``.

        Idempotent: if the job is already in a terminal state (a race
        between this call and a concurrent admin action), we leave it
        alone instead of re-emitting the metric.
        """
        job = await self._jobs.get(job_id)
        if job is None:
            _logger.error("terminal_fail_job_not_found", job_id=job_id)
            return
        if job.status in (JobStatus.DONE, JobStatus.FAILED):
            _logger.info(
                "terminal_fail_already_terminal",
                job_id=job_id,
                status=job.status.value,
            )
            return

        reason = classify_exception(exc)
        if isinstance(exc, AppError):
            user_text = exc.user_message
        else:
            user_text = "Не удалось обработать запрос."

        with bind_context(job_id=job.id, user_id=job.user_id, chat_id=job.chat_id):
            await self._fail(job, user_text, repr(exc), reason)

    async def _fail(
        self,
        job: DownloadJob,
        user_text: str,
        error_repr: str,
        reason: ReasonClass,
    ) -> None:
        previous = job.status
        job.mark_failed(error_repr)
        saved = await self._save_unless_terminal(
            job,
            conflict_event="job_fail_conflict_ignored",
            attempted_status=job.status.value,
        )
        if saved is None:
            return
        job = saved
        self._emit_transition(previous, JobStatus.FAILED, reason)
        if job.id is not None:
            try:
                await self._reporter.fail(job_id=job.id, reason=user_text)
            except Exception:  # pragma: no cover  defensive
                _logger.exception("progress_fail_failed", job_id=job.id)
        try:
            await self._sender.send_text(job.chat_id, f"⚠️ {user_text}")
        except RecipientUnavailableError:
            # Blocked the bot or left the chat: nobody to tell, not an error.
            _logger.info("notify_user_skipped_recipient_unavailable")
        except Exception:  # pragma: no cover  best-effort
            _logger.exception("notify_user_about_failure_failed")

    async def _check_cancel(self, job_id: int | None) -> None:
        """Raise :class:`JobCancelledError` if the user cancelled this job.

        No-op when no cancellation store is wired (tests / legacy
        branch). The store swallows its own Redis errors and returns
        ``False`` on outage — we never block progress on a flag-store
        flake.
        """
        if job_id is None or self._cancellation is None:
            return
        if await self._cancellation.is_cancelled(job_id=job_id):
            raise JobCancelledError(f"job {job_id} cancelled by user")

    async def _handle_cancellation(self, job: DownloadJob) -> None:
        """Reconcile a cancelled job: DB → FAILED row, Redis → clear flag."""
        # Reuse FAILED status to avoid a DB migration in this PR; the
        # user-visible stage on the reporter is still CANCELLED, which
        # is the distinction the UI cares about. See docs/tasks/archive/
        # instant-download-ux.md §4 PR 5 "cancel MVP".
        previous = job.status
        job.mark_failed("cancelled by user")
        saved = await self._save_unless_terminal(job, conflict_event="job_cancel_conflict_ignored")
        if saved is None:
            return
        job = saved
        # USER_ERROR is the right SLO bucket — cancellation is the
        # user's choice, not a service regression. Matches how
        # ``TooManyJobsError`` is classified in reason_class.py.
        self._emit_transition(previous, JobStatus.FAILED, ReasonClass.USER_ERROR)
        if self._cancellation is not None and job.id is not None:
            try:
                await self._cancellation.clear(job_id=job.id)
            except Exception:  # pragma: no cover  defensive
                _logger.exception("cancel_flag_clear_failed", job_id=job.id)

    async def _save_unless_terminal(
        self, job: DownloadJob, *, conflict_event: str, **log_fields: str
    ) -> DownloadJob | None:
        """Write ``job``; ``None`` when a concurrent writer already made
        the row terminal (DONE / FAILED), any other conflict re-raises."""
        try:
            return await self._jobs.update(job)
        except JobConcurrentUpdateError:
            latest = await self._jobs.get(job.id or 0)
            if latest is not None and latest.status in (JobStatus.DONE, JobStatus.FAILED):
                _logger.warning(
                    conflict_event,
                    job_id=job.id,
                    **log_fields,
                    current_status=latest.status.value,
                )
                return None
            raise

    async def _persist_done(self, job: DownloadJob) -> DownloadJob:
        try:
            return await self._jobs.update(job)
        except JobConcurrentUpdateError:
            latest = await self._jobs.get(job.id or 0)
            if latest is None:
                raise
            _logger.warning(
                "job_done_conflict_detected",
                job_id=job.id,
                current_status=latest.status.value,
                current_status_version=latest.status_version,
            )
            if latest.status is JobStatus.DONE:
                return latest
            if latest.status is JobStatus.FAILED:
                latest.title = job.title
                latest.media_id = job.media_id
                latest.selected_format = job.selected_format
                latest.mark_done(
                    file_path=job.file_path,
                    file_size=job.file_size,
                    mime_type=job.mime_type,
                    telegram_file_id=job.telegram_file_id,
                    public_url=job.public_url,
                )
                return await self._jobs.update(latest)
            raise

    async def _emit_progress(
        self,
        job_id: int | None,
        *,
        percent: float,
        stage: ProgressStage,
    ) -> None:
        """Forward a phase-boundary progress event to the reporter.

        Swallows every exception: ADR-0010 §2.2 forbids progress
        bookkeeping from breaking the main download. ``job.id`` can be
        ``None`` for synthetic fixtures (tests construct a DownloadJob
        without persisting it); those calls are no-ops.
        """
        if job_id is None:
            return
        try:
            await self._reporter.update(job_id=job_id, percent=percent, stage=stage)
        except Exception:  # pragma: no cover  defensive
            _logger.exception("progress_emit_failed", job_id=job_id, stage=stage.value)

    def _scaled_download_hook(
        self,
        job_id: int | None,
        *,
        provider: Provider,
    ) -> Callable[[float], None] | None:
        """Build the sync callback yt-dlp writes percent into.

        We scale the 0..100 yt-dlp percent into 0..70 so the reported
        number never overshoots the PROCESSING boundary. Also acts as a
        seam for tests: a ``NoopProgressReporter`` does not implement
        ``download_hook`` (by design — it is not part of the Protocol),
        so we introspect the concrete class.
        """
        if job_id is None:
            return None
        build = getattr(self._reporter, "download_hook", None)
        if build is None:
            return None
        # ``provider`` is available in case a future hook wants to
        # distinguish YT vs IG (e.g. different percent scaling). Not
        # used yet — reference kept so the signature stays stable.
        del provider
        inner = build(job_id=job_id)

        def _scaled(pct: float) -> None:
            scaled = max(0.0, min(70.0, pct * 0.7))
            inner(scaled)

        return _scaled
