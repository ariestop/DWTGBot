"""Worker-side orchestration of a single download job."""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.application.services.delivery_service import DeliveryService
from app.application.services.job_metrics import JobMetrics, NoopJobMetrics
from app.application.services.providers import ProviderRegistry
from app.config import Settings
from app.domain.entities.download_job import DownloadJob
from app.domain.entities.media_info import DownloadOption
from app.domain.enums import JobStatus
from app.domain.observability import file_size_class
from app.domain.reason_class import ReasonClass, classify_exception
from app.domain.repositories.jobs_repo import JobsRepository
from app.exceptions import AppError, FileTooLargeError
from app.infrastructure.storage.local_storage import LocalStorage
from app.infrastructure.telegram.sender import TelegramSender
from app.logging_config import get_logger
from app.utils.correlation import bind_context

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProcessDownloadInput:
    job_id: int
    correlation_id: str


class ProcessDownloadUseCase:
    def __init__(
        self,
        *,
        jobs_repo: JobsRepository,
        providers: ProviderRegistry,
        storage: LocalStorage,
        delivery: DeliveryService,
        sender: TelegramSender,
        settings: Settings,
        metrics: JobMetrics | None = None,
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
            await self._run(job)

    async def _run(self, job: DownloadJob) -> None:
        # Monotonic clock — immune to wall-clock jumps from NTP. Used
        # for ``job_duration_seconds`` (ADR-0007 §2.1, A4/A5 SLO).
        started_monotonic = time.monotonic()

        await self._transition_to(job, JobStatus.PROCESSING, ReasonClass.OK)

        try:
            provider = self._providers.get(job.platform)
            info = await provider.get_info(job.source_url)
            options = provider.build_options(info)
            selected = self._pick_option(options, job.selected_option_key)
            if selected is None:
                raise AppError(
                    f"Selected option not available anymore: {job.selected_option_key}",
                    user_message="Выбранный вариант больше недоступен. Пришлите ссылку заново.",
                )

            self._reject_if_estimate_exceeds_cap(selected)

            target = self._storage.job_dir(job.id or 0)
            result = await provider.download(job.source_url, selected, target_dir=str(target))

            outcome = await self._delivery.deliver(
                job_id=job.id or 0,
                chat_id=job.chat_id,
                result=result,
            )

            job.title = info.title
            job.media_id = info.media_id
            job.selected_format = selected.container or selected.label
            job.mark_done(
                file_path=outcome.delivered_path,
                file_size=outcome.file_size,
                mime_type=result.primary_mime,
                telegram_file_id=outcome.primary_telegram_file_id,
                public_url=outcome.public_url,
            )
            await self._jobs.update(job)

            # Duration first so the Histogram + the Counter land on the
            # same scrape; then the status-change + log keep the events
            # paired (P11).
            duration_seconds = time.monotonic() - started_monotonic
            size_class = file_size_class(outcome.file_size)
            self._metrics.observe_job_duration(file_size_class=size_class, seconds=duration_seconds)
            self._metrics.inc_status_change(to=JobStatus.DONE, reason_class=ReasonClass.OK)
            _logger.info(
                "job_done",
                method=outcome.method.value,
                size=outcome.file_size,
                total_seconds=round(duration_seconds, 3),
                file_size_class=size_class.value,
            )
            _logger.info(
                "job_status_changed",
                from_=JobStatus.PROCESSING.value,
                to=JobStatus.DONE.value,
                reason_class=ReasonClass.OK.value,
            )

        except AppError as exc:
            reason = classify_exception(exc)
            if exc.is_retryable:
                # Re-raise so arq increments ``job_try`` and re-schedules
                # with backoff. The job row stays in ``PROCESSING`` —
                # cleaner than flapping back to PENDING — and the
                # task-level wrapper marks it FAILED only when arq
                # exhausts ``max_tries`` (see infrastructure/queue/tasks.py).
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

    def _reject_if_estimate_exceeds_cap(self, option: DownloadOption) -> None:
        """Fail fast (P10) when the provider's own estimate already exceeds
        ``MAX_FILE_SIZE_MB``. The post-download check in ``LocalStorage`` and
        ``DeliveryService`` is still authoritative; this just avoids burning
        bandwidth/disk on a job we are guaranteed to refuse."""
        estimate = option.estimated_size_bytes
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
    ) -> None:
        """Mutate-persist-emit. Centralised so every status change goes
        through one place that owns metric + log emission. The DB
        update has to happen before the metric so a Prometheus scrape
        racing the failure path can never observe a transition that
        Postgres rejected (rare, but keeps the dashboard honest).

        ``mark_processing`` is the only mutator we call from here for
        now; ``mark_done`` / ``mark_failed`` carry extra payload and
        are inlined in ``_run`` / ``_fail`` to avoid an awkward
        signature.
        """
        previous = job.status
        if target is JobStatus.PROCESSING:
            job.mark_processing()
        else:  # pragma: no cover  defensive
            raise ValueError(f"_transition_to does not handle {target}")
        await self._jobs.update(job)
        self._metrics.inc_status_change(to=target, reason_class=reason)
        _logger.info(
            "job_status_changed",
            from_=previous.value,
            to=target.value,
            reason_class=reason.value,
        )

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
        try:
            await self._jobs.update(job)
        finally:
            self._metrics.inc_status_change(to=JobStatus.FAILED, reason_class=reason)
            _logger.info(
                "job_status_changed",
                from_=previous.value,
                to=JobStatus.FAILED.value,
                reason_class=reason.value,
            )
            try:
                await self._sender.send_text(job.chat_id, f"⚠️ {user_text}")
            except Exception:  # pragma: no cover  best-effort
                _logger.exception("notify_user_about_failure_failed")
