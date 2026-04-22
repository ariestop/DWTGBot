"""Text-message handler that treats any URL-bearing message as a download request."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.application.dto.media import AnalyzedMedia
from app.application.services.rate_limit import evaluate as evaluate_rate_limit
from app.application.use_cases.auto_enqueue_download import AutoEnqueueInput
from app.bot.callbacks.codec import CancelJobCallback
from app.bot.container import BotContainer, get_container
from app.bot.keyboards.download_options import build_options_keyboard
from app.domain.enums import Platform
from app.domain.rate_limit import LimitDecision
from app.exceptions import AppError, InvalidUrlError, TooManyJobsError, UnsupportedPlatformError
from app.logging_config import get_logger
from app.utils.correlation import bind_context, new_request_id
from app.utils.url import extract_first_url, normalize_domain

_logger = get_logger(__name__)


_RETRY_TEMPLATE = (
    "⏳ Слишком много запросов. Попробуйте снова через {n} {unit}.\n(лимит: {category})"
)
_ANALYZE_CAPTION = "Анализирую ссылку…"


def _format_retry_after(seconds: int) -> tuple[int, str]:
    """Round to a human-friendly grain per docs/36- §4.1."""
    if seconds <= 5:
        return 5, "секунд"
    if seconds <= 30:
        return 30, "секунд"
    if seconds <= 60:
        return 60, "секунд"
    minutes = max(1, (seconds + 59) // 60)
    return minutes, "минут" if minutes != 1 else "минуту"


def _format_user_message(decision: LimitDecision) -> str:
    n, unit = _format_retry_after(decision.retry_after_s)
    category = decision.layer.user_facing_category if decision.layer else "personal"
    return _RETRY_TEMPLATE.format(n=n, unit=unit, category=category)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: PLR0911
    # Seven early returns, each is a distinct guard: missing message,
    # rate-limit silent-drop, rate-limit reply, invalid URL, provider
    # app error, unexpected provider error, no options. Flattening
    # these would obscure the log events tied to each branch.
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None or not message.text:
        return

    container = get_container(context.bot_data)
    correlation_id = new_request_id()

    with bind_context(
        request_id=correlation_id,
        user_id=user.id,
        chat_id=chat.id,
    ):
        # Rate limiting (docs/36- §2). The check runs at the *intake* edge,
        # before any provider/network work — so a denied user costs us only a
        # handful of Redis ops. ``evaluate`` walks every layer in §2.1 order
        # and short-circuits on the first deny. When ``RL_ENABLED=false``
        # the gate is a Noop and this is effectively a no-op.
        first_url = extract_first_url(message.text)
        domain = normalize_domain(first_url) if first_url else None
        decision = await evaluate_rate_limit(
            user_id=user.id,
            chat_id=chat.id,
            domain=domain,
            gate=container.rate_limit_gate,
            windows=container.settings.rate_limit_windows,
            metrics=container.metrics,
            request_id=correlation_id,
        )
        if not decision.allowed:
            scope_key = f"u:{user.id}"
            should_speak = await container.notice_throttle.should_notify(
                scope_key=scope_key,
                ttl_s=container.settings.RL_NOTICE_TTL,
            )
            if should_speak:
                if decision.layer is not None:
                    container.metrics.inc_first_deny(layer=decision.layer, user_id=user.id)
                _logger.info(
                    "rate_limit_first_deny",
                    user_id=user.id,
                    chat_id=chat.id,
                    layer=decision.layer.value if decision.layer else None,
                    retry_after_s=decision.retry_after_s,
                    request_id=correlation_id,
                )
                await message.reply_text(_format_user_message(decision))
            return

        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)

        try:
            result = await container.analyze_link.execute(message.text)
        except (InvalidUrlError, UnsupportedPlatformError) as exc:
            _logger.info("link_rejected", reason=type(exc).__name__)
            await message.reply_text(exc.user_message)
            return
        except AppError as exc:
            _logger.warning("link_analysis_failed", error=str(exc))
            await message.reply_text(exc.user_message)
            return
        except Exception as exc:
            _logger.exception("link_analysis_unexpected_error", error=str(exc))
            await message.reply_text("Не удалось обработать ссылку. Попробуйте позже.")
            return

        if not result.analyzed.options:
            await message.reply_text(
                "Не удалось получить ни одного варианта скачивания для этого медиа."
            )
            return

        # YouTube opts out of the instant flow because the resolution
        # picker is the whole point of the UX there — a YT link can
        # legitimately mean "360p because I'm on cellular" just as
        # often as "1080p because I'm on wifi", and the instant path
        # always picks ``max(video_heights)`` which burns the user's
        # quota on MB they did not want. Instagram / TikTok / etc.
        # have exactly one shape per post, so instant stays the
        # default there. This is the narrowest possible per-platform
        # override of the ``INSTANT_DOWNLOAD_ENABLED`` flag — both
        # branches below are unchanged.
        picker_platforms = {Platform.YOUTUBE}
        use_instant = (
            container.settings.INSTANT_DOWNLOAD_ENABLED
            and result.analyzed.info.platform not in picker_platforms
        )
        if use_instant:
            await _handle_instant_download(
                message=message,
                chat_id=chat.id,
                user_id=user.id,
                container=container,
                analyzed=result.analyzed,
                correlation_id=correlation_id,
            )
            return

        # Legacy picker flow — preserved behind the flag so an operator
        # can roll back by flipping ``INSTANT_DOWNLOAD_ENABLED=false``
        # without a redeploy (ADR-0010 §2.1 risk R1).
        info = result.analyzed.info
        keyboard = build_options_keyboard(result.request_id, result.analyzed.options)
        title = info.title or "медиа"
        await message.reply_text(
            f"<b>{_escape(title)}</b>\nВыберите вариант:",
            parse_mode="HTML",
            reply_markup=keyboard,
        )


async def _handle_instant_download(
    *,
    message,
    chat_id: int,
    user_id: int,
    container: BotContainer,
    analyzed: AnalyzedMedia,
    correlation_id: str,
) -> None:
    """Send a placeholder + auto-enqueue.

    Ordering matters: we send the placeholder *before* we call
    ``auto_enqueue.execute`` so the reporter has a ``message_id`` to
    persist in ``progress_meta:{job_id}`` right away. If enqueue fails
    we edit the placeholder in-place to the error so the user sees one
    coherent message lifecycle instead of two.
    """
    info = analyzed.info
    placeholder = await _send_placeholder(message, thumbnail_url=info.thumbnail_url)
    if placeholder is None:
        # Telegram rejected both the photo and the text fallback —
        # nothing we can do beyond the (now absent) UI. Log and drop.
        _logger.error("instant_placeholder_failed")
        return

    try:
        enqueue_result = await container.auto_enqueue_download.execute(
            AutoEnqueueInput(
                user_id=user_id,
                chat_id=chat_id,
                analyzed=analyzed,
                source_url=_resolve_source_url(analyzed),
                placeholder_message_id=placeholder.message_id,
                correlation_id=correlation_id,
            )
        )
    except TooManyJobsError as exc:
        _logger.info("instant_enqueue_rejected_cap", reason=str(exc))
        await _edit_placeholder_text(placeholder, exc.user_message)
        return
    except AppError as exc:
        _logger.warning("instant_enqueue_failed", error=str(exc))
        await _edit_placeholder_text(placeholder, exc.user_message)
        return
    except Exception as exc:
        _logger.exception("instant_enqueue_unexpected_error", error=str(exc))
        await _edit_placeholder_text(placeholder, "Не удалось поставить задачу в очередь.")
        return

    # Attach the cancel button now. The initial caption stays
    # "Анализирую ссылку…" — the progress_updater will flip it to
    # "Скачиваю" as soon as the worker emits the first event.
    # ``edit_reply_markup`` works identically for photo and text
    # messages (PTB normalises), so a single call covers both paths.
    cancel_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ Отменить",
                    callback_data=CancelJobCallback(job_id=enqueue_result.job_id).encode(),
                )
            ]
        ],
    )
    try:
        await placeholder.edit_reply_markup(reply_markup=cancel_keyboard)
    except TelegramError:
        # Non-fatal: the user can still wait for the auto-delete on
        # DONE / CANCELLED. The cancel button is a convenience, not a
        # correctness requirement.
        _logger.warning("instant_attach_cancel_failed", job_id=enqueue_result.job_id)


async def _send_placeholder(message, *, thumbnail_url: str | None):
    """Send photo+caption if we have a thumbnail, else plain text.

    Returns the Telegram ``Message`` object on success, ``None`` if the
    client rejected both paths. Telegram can reject a remote-URL photo
    for many reasons (wrong content-type, 403 on the CDN, etc.) — the
    text fallback keeps the UX intact.
    """
    if thumbnail_url:
        try:
            return await message.reply_photo(
                photo=thumbnail_url,
                caption=_ANALYZE_CAPTION,
            )
        except TelegramError:
            _logger.info("instant_placeholder_photo_failed", thumbnail=thumbnail_url)
    try:
        return await message.reply_text(_ANALYZE_CAPTION)
    except TelegramError:
        _logger.exception("instant_placeholder_text_failed")
        return None


async def _edit_placeholder_text(placeholder, text: str) -> None:
    """Replace a placeholder's caption/text with an error message.

    Tolerant of both photo (``edit_caption``) and text
    (``edit_text``) placeholders so the error UX is uniform.
    """
    try:
        if placeholder.photo:
            await placeholder.edit_caption(caption=text)
        else:
            await placeholder.edit_text(text=text)
    except TelegramError:
        # Last-ditch fallback — the placeholder may have been deleted
        # by the user; we don't want to drag an AppError up the stack.
        _logger.info("instant_placeholder_edit_failed")


def _resolve_source_url(analyzed: AnalyzedMedia) -> str:
    """Best-match source URL from provider ``raw`` bag.

    Mirrors the picker flow's helper in
    ``app/bot/callbacks/download.py`` so both paths persist the
    same ``source_url`` into ``download_jobs.source_url``.
    """
    raw = analyzed.info.raw or {}
    for key in ("source_url", "webpage_url", "original_url"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
