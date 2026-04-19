"""Text-message handler that treats any URL-bearing message as a download request."""

from __future__ import annotations

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from app.application.services.rate_limit import evaluate as evaluate_rate_limit
from app.bot.container import get_container
from app.bot.keyboards.download_options import build_options_keyboard
from app.domain.rate_limit import LimitDecision
from app.exceptions import AppError, InvalidUrlError, UnsupportedPlatformError
from app.logging_config import get_logger
from app.utils.correlation import bind_context, new_request_id
from app.utils.url import extract_first_url, normalize_domain

_logger = get_logger(__name__)


_RETRY_TEMPLATE = (
    "⏳ Слишком много запросов. Попробуйте снова через {n} {unit}.\n(лимит: {category})"
)


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


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            # §4.3 — only the first deny per RL_NOTICE_TTL window is loud.
            # ``should_notify`` is also our "first deny" oracle: if it
            # returns True, this is the first deny in the current window
            # for this user, and we count it for §8.1 ("noisy users" topk).
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

        info = result.analyzed.info
        keyboard = build_options_keyboard(result.request_id, result.analyzed.options)
        title = info.title or "медиа"
        await message.reply_text(
            f"<b>{_escape(title)}</b>\nВыберите вариант:",
            parse_mode="HTML",
            reply_markup=keyboard,
        )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
