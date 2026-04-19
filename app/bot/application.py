"""
Build a configured ``telegram.ext.Application``.

Wire-up of concrete infrastructure (providers, queue, repositories, redis)
is delegated to the entrypoint (``app.main_bot``) which constructs the
container and passes it in. This module only deals with PTB plumbing.
"""

from __future__ import annotations

from telegram import BotCommand
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.bot.callbacks.download import handle_download_callback
from app.bot.container import CONTAINER_KEY, BotContainer
from app.bot.handlers.commands import about, health, help_cmd, start
from app.bot.handlers.errors import on_error
from app.bot.handlers.links import handle_link
from app.bot.middleware.logging_mw import bind_request_context
from app.bot.middleware.metrics_mw import instrument
from app.config import Settings


def build_application(settings: Settings, container: BotContainer) -> Application:
    builder = (
        ApplicationBuilder()
        .token(settings.BOT_TOKEN)
        .concurrent_updates(True)
        .rate_limiter(AIORateLimiter())
        .post_init(_on_post_init)
    )
    application: Application = builder.build()
    application.bot_data[CONTAINER_KEY] = container

    # Pseudo-middleware: bind correlation context first.
    application.add_handler(TypeHandler(object, bind_request_context), group=-100)

    # Each user-facing handler is wrapped with ``instrument`` (ADR-0007
    # §2.1, app/bot/middleware/metrics_mw.py) so we get one
    # ``bot_message_handled`` event + counter increment per update,
    # regardless of which handler matched. Wrapping at registration
    # time keeps handler bodies free of metrics noise.
    application.add_handler(CommandHandler("start", instrument(start)))
    application.add_handler(CommandHandler("help", instrument(help_cmd)))
    application.add_handler(CommandHandler("about", instrument(about)))
    application.add_handler(CommandHandler("health", instrument(health)))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, instrument(handle_link))
    )

    application.add_handler(CallbackQueryHandler(instrument(handle_download_callback)))

    application.add_error_handler(on_error)
    return application


async def _on_post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Приветствие"),
            BotCommand("help", "Справка"),
            BotCommand("health", "Диагностика"),
            BotCommand("about", "О проекте"),
        ]
    )
