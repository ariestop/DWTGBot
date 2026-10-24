"""Domain-wide exception hierarchy.

All caught exceptions in user-facing code paths should derive from
:class:`AppError` so handlers can render predictable user messages.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application errors.

    The ``is_retryable`` class attribute drives the worker's retry
    decision (see ``ProcessDownloadUseCase._run`` and
    ``infrastructure/queue/tasks.py``). Subclasses override it to
    flip the default per error class:

      - transient I/O / upstream 5xx        → retryable
      - user input / private content / 4xx  → permanent

    A retryable subclass can still be re-pinned to permanent by
    overriding the flag (see ``MediaPrivateError`` etc.).
    """

    user_message: str = "Что-то пошло не так. Попробуйте позже."
    is_retryable: bool = False

    def __init__(self, message: str | None = None, *, user_message: str | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        if user_message is not None:
            self.user_message = user_message


# ----- Configuration / startup -----


class ConfigError(AppError):
    user_message = "Сервис временно недоступен (ошибка конфигурации)."


# ----- URL / input -----


class InvalidUrlError(AppError):
    user_message = "Это не похоже на ссылку. Пришлите URL целиком."


class UnsupportedPlatformError(AppError):
    user_message = "Эту площадку я пока не поддерживаю."


# ----- Provider / metadata -----


class ProviderError(AppError):
    """Generic upstream failure — assume transient unless a more specific
    subclass overrides."""

    user_message = "Не удалось получить информацию о медиа."
    is_retryable = True


class MediaNotFoundError(ProviderError):
    user_message = "Медиа не найдено или удалено."
    is_retryable = False  # 404 from upstream: a retry will not change the answer.


class MediaPrivateError(ProviderError):
    user_message = "Контент приватный или требует авторизации."
    is_retryable = False  # 403 from upstream: requires user-side action, not a retry.


# ----- Download / processing -----


class DownloadError(AppError):
    """Generic download failure — assume transient (network, 5xx, rate
    limit on upstream). Permanent variants override below."""

    user_message = "Не удалось скачать файл. Попробуйте ещё раз."
    is_retryable = True


class DownloadTimeoutError(DownloadError):
    """Per-attempt yt-dlp timeout.

    We treat it as terminal: the underlying ``asyncio.to_thread`` worker
    cannot be force-cancelled, so retrying immediately tends to keep a slot
    occupied while the previous hung download is still winding down.
    """

    user_message = "Скачивание заняло слишком много времени."
    is_retryable = False


class FileTooLargeError(DownloadError):
    user_message = "Файл слишком большой даже для временной ссылки."
    is_retryable = False  # the file size is fixed; a retry cannot shrink it.


class SizeUnknownError(DownloadError):
    """Raised when we cannot determine a video's size before download."""

    user_message = "Не удалось заранее определить размер файла. Попробуйте другую ссылку."
    is_retryable = False


class FfmpegError(DownloadError):
    """ffmpeg failures are usually about the source file, not transient
    state — re-encoding the same input would fail the same way."""

    user_message = "Не удалось обработать медиа."
    is_retryable = False


# ----- Storage / temp links -----


class StorageError(AppError):
    user_message = "Ошибка хранилища."


class JobConcurrentUpdateError(AppError):
    """Optimistic-lock conflict while persisting a job row."""

    user_message = "Задача обновлялась параллельно. Попробуйте ещё раз."
    is_retryable = True


class TempLinkExpiredError(AppError):
    user_message = "Ссылка устарела."


class TempLinkExhaustedError(AppError):
    user_message = "Лимит скачиваний по этой ссылке исчерпан."


# ----- Rate / quota -----


class TooManyJobsError(AppError):
    """Raised when a single user has too many in-flight jobs."""

    user_message = (
        "Сейчас у вас уже выполняется несколько задач. Дождитесь их завершения и попробуйте снова."
    )


# ----- Circuit breaker (L5) -----


class UpstreamUnavailableError(DownloadError):
    """Raised by the yt-dlp circuit breaker when the per-host breaker is
    open, and by ``classify_error`` for throttle-shaped yt-dlp errors
    (HTTP 429, "rate-limit"). Inherits ``is_retryable=True`` from ``DownloadError`` so arq
    will reschedule — by the time the next attempt runs the breaker
    may already have closed."""

    user_message = "Источник временно ограничивает скачивание. Попробуйте через несколько минут."


# ----- Delivery to Telegram -----
#
# TelegramSender translates PTB errors into these, so the application
# layer never imports ``telegram`` (ADR-0012).


class DeliveryError(AppError):
    """Base for a failed send to Telegram."""

    user_message = "Не удалось отправить файл в Telegram. Попробуйте позже."


class DeliveryTransientError(DeliveryError):
    """Timeout, network error or flood control (``RetryAfter``): worth
    retrying, in place first and then as a whole job."""

    is_retryable = True

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_s: float | None = None,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message, user_message=user_message)
        self.retry_after_s = retry_after_s


class DeliveryRejectedError(DeliveryError):
    """Telegram answered ``BadRequest``. Retried in place (a CDN blip
    sometimes clears), but a new download would get the same answer."""

    user_message = "Telegram не принял файл. Попробуйте другое качество или другую ссылку."
    is_retryable = False


class RecipientUnavailableError(DeliveryError):
    """The chat cannot receive messages: the user blocked the bot, the
    bot left the group, or the group moved. Never retried."""

    user_message = "Не удалось отправить сообщение в этот чат."
    is_retryable = False


# ----- Cancellation (ADR-0010 §2.1 cancel-job) -----


class JobCancelledError(AppError):
    """Raised when a phase-boundary check sees a live ``cancel:{job_id}`` flag.

    Non-retryable by design — the user asked to stop, re-queueing would
    undo the explicit intent. The worker catches this separately from
    generic ``AppError`` so we never emit the ``⚠️`` user-visible notice
    (DeliveryService never runs) and the reporter terminal stage is
    ``CANCELLED`` instead of ``FAILED``.
    """

    user_message = "Отменено."
    is_retryable = False
