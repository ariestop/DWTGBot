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
    """Inherits ``is_retryable=True`` — timeouts are by definition transient."""

    user_message = "Скачивание заняло слишком много времени."


class FileTooLargeError(DownloadError):
    user_message = "Файл слишком большой даже для временной ссылки."
    is_retryable = False  # the file size is fixed; a retry cannot shrink it.


class FfmpegError(DownloadError):
    """ffmpeg failures are usually about the source file, not transient
    state — re-encoding the same input would fail the same way."""

    user_message = "Не удалось обработать медиа."
    is_retryable = False


# ----- Storage / temp links -----


class StorageError(AppError):
    user_message = "Ошибка хранилища."


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
    open. Inherits ``is_retryable=True`` from ``DownloadError`` so arq
    will reschedule — by the time the next attempt runs the breaker
    may already have closed."""

    user_message = "Источник временно ограничивает скачивание. Попробуйте через несколько минут."
