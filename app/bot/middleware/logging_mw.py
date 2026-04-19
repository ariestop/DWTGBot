"""
Pseudo-middleware: a low-priority TypeHandler that binds correlation context
for every Update before specific handlers run.

PTB has no traditional middleware; ``group=-100`` ensures this runs first.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.logging_config import get_logger
from app.utils.correlation import bind_context, new_request_id

_logger = get_logger(__name__)


async def bind_request_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    update_id = update.update_id

    request_id = new_request_id()
    # We use the context manager pattern just to bind; we don't need to unbind
    # within a single update because PTB runs each update in its own task.
    with bind_context(request_id=request_id, user_id=user_id, chat_id=chat_id, update_id=update_id):
        _logger.debug(
            "update_received", update_type=update.effective_message and "message" or "other"
        )
