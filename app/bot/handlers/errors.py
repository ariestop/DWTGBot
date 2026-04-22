"""Global error handler for python-telegram-bot."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.exceptions import AppError
from app.logging_config import get_logger

_logger = get_logger(__name__)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    # Non-AppError paths are almost always transient at this layer
    # (Instagram 429, Telegram RetryAfter, brief Redis blip). The
    # previous "Внутренняя ошибка. Уже разбираемся." read like a
    # ticketed server bug and scared users into not retrying — even
    # though a second attempt usually went through. Say so instead.
    user_message = "Что-то пошло не так. Попробуйте отправить ссылку ещё раз."
    if isinstance(err, AppError):
        user_message = err.user_message

    _logger.exception("bot_error", error=str(err))

    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(user_message)
        except Exception:  # pragma: no cover  best-effort reply
            _logger.exception("error_handler_reply_failed")
