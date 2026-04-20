"""Bot process entrypoint."""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

from telegram.ext import Application

from app.bot.application import build_application
from app.composition import BotComposition, build_bot
from app.config import get_settings
from app.logging_config import configure_logging, get_logger
from app.observability.sentry import configure_sentry


async def _finalize_bot_run(
    application: Application,
    composition: BotComposition,
    *,
    app_initialized: bool,
    log: Any,
) -> None:
    log.info("bot_stopping")
    try:
        if application.updater is not None:
            try:
                await application.updater.stop()
            except RuntimeError as exc:
                # If ``initialize()`` failed, the updater was never started.
                if "not running" not in str(exc).lower():
                    raise
        if app_initialized:
            await application.stop()
    finally:
        await application.shutdown()
        await composition.aclose()
    log.info("bot_stopped")


async def _amain() -> int:
    settings = get_settings()
    configure_logging(settings)
    configure_sentry(settings, role="bot")
    log = get_logger("dwtgbot.main_bot")

    errors = settings.validate_runtime(require_storage=False, require_tools=False)
    if errors:
        for err in errors:
            log.error("startup_check_failed", reason=err)
        return 2

    composition = await build_bot(settings)
    application = build_application(settings, composition.container)
    app_initialized = False

    log.info("bot_starting", env=settings.APP_ENV.value)
    try:
        # Start /metrics first so probes can hit it during application init
        # (composition.metrics_server is None when METRICS_ENABLED=false).
        if composition.metrics_server is not None:
            start = getattr(composition.metrics_server, "start", None)
            if start is not None:
                await start()
        # Queue-depth sampler runs alongside the bot — also Noop'd
        # when METRICS_ENABLED=false (composition.queue_sampler stays
        # None). ADR-0007 §2.6.
        if composition.queue_sampler is not None:
            start = getattr(composition.queue_sampler, "start", None)
            if start is not None:
                await start()
        await application.initialize()
        app_initialized = True
        await application.start()
        # ``application.updater`` is typed as ``Updater | None`` because PTB
        # supports webhook-only setups; we always run polling so it must be
        # present here. Fail loud if PTB ever changes that contract.
        if application.updater is None:
            raise RuntimeError("PTB Application has no Updater (polling mode required)")
        await application.updater.start_polling(drop_pending_updates=False)
        stop_event = asyncio.Event()

        def _stop(*_: object) -> None:
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _stop)

        await stop_event.wait()
    finally:
        await _finalize_bot_run(
            application,
            composition,
            app_initialized=app_initialized,
            log=log,
        )
    return 0


def main() -> None:
    sys.exit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
