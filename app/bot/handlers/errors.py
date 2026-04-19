"""Global error handler for python-telegram-bot."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.exceptions import AppError
from app.logging_config import get_logger

_logger = get_logger(__name__)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    user_message = "Внутренняя ошибка. Уже разбираемся."
    if isinstance(err, AppError):
        user_message = err.user_message

    _logger.exception("bot_error", error=str(err))

    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(user_message)
        except Exception:  # pragma: no cover  best-effort reply
            _logger.exception("error_handler_reply_failed")
