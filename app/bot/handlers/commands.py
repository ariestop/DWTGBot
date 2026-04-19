"""Slash-command handlers: /start /help /health /about."""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app import __version__
from app.bot.container import get_container
from app.logging_config import get_logger

_logger = get_logger(__name__)


HELP_TEXT = (
    "<b>DWTGBot</b> — медиа-загрузчик.\n\n"
    "Поддерживаются:\n"
    "• YouTube (видео и аудио)\n"
    "• Instagram (видео, фото, карусели)\n\n"
    "Просто пришлите ссылку — я предложу варианты скачивания.\n\n"
    "<b>Команды</b>\n"
    "/start — приветствие\n"
    "/help — эта справка\n"
    "/health — диагностика\n"
    "/about — версия и сведения о проекте\n"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "Привет! Пришлите ссылку на YouTube или Instagram — и я скачаю медиа.",
        parse_mode=ParseMode.HTML,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    container = get_container(context.bot_data)
    text = (
        f"<b>DWTGBot</b> v{__version__}\n"
        f"Окружение: <code>{container.settings.APP_ENV.value}</code>\n"
        "Open-source media downloader."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Self-diagnostic command.

    Reports settings overview and basic readiness. Heavy checks (DB ping,
    Redis ping) are deliberately delegated to /healthz HTTP endpoint;
    here we just confirm the bot is alive and report config.
    """
    if update.effective_message is None:
        return
    container = get_container(context.bot_data)
    s = container.settings
    text = (
        "✅ Бот жив.\n"
        f"env: {s.APP_ENV.value}\n"
        f"role: {s.APP_ROLE.value}\n"
        f"telegram limit: {s.TELEGRAM_MAX_UPLOAD_MB} MB\n"
        f"temp link TTL: {s.TEMP_LINK_TTL_SECONDS}s\n"
    )
    _logger.info(
        "health_command", chat_id=update.effective_chat.id if update.effective_chat else None
    )
    await update.effective_message.reply_text(text)
