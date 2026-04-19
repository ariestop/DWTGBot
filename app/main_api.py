"""FastAPI process entrypoint."""

from __future__ import annotations

import sys

import uvicorn

from app.api.app import create_app
from app.config import get_settings
from app.logging_config import configure_logging, get_logger


def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("dwtgbot.main_api")

    errors = settings.validate_runtime(require_storage=True, require_tools=False)
    if errors:
        for err in errors:
            log.error("api_startup_check_failed", reason=err)
        return 2

    app = create_app(settings)
    log.info("api_starting", host=settings.API_HOST, port=settings.API_PORT)
    uvicorn.run(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level=settings.LOG_LEVEL.lower(),
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
