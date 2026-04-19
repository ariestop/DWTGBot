"""
Centralized logging configuration based on structlog.

- JSON output in production, human-friendly in development.
- Correlation context (request_id / job_id / user_id / chat_id) is injected
  via structlog contextvars and propagated through async boundaries.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from app.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging. Idempotent."""
    level = getattr(logging, settings.LOG_LEVEL, logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.LOG_JSON or settings.is_production:
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging into structlog-rendered handlers so libs
    # (httpx, sqlalchemy, telegram) emit consistent records.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StdlibFormatter(renderer=renderer, shared=shared_processors))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for noisy in ("httpx", "httpcore", "telegram.ext.Application", "asyncio"):
        logging.getLogger(noisy).setLevel(max(level, logging.INFO))


class _StdlibFormatter(logging.Formatter):
    """Adapter that runs stdlib LogRecords through structlog processors."""

    def __init__(self, renderer: Processor, shared: list[Processor]) -> None:
        super().__init__()
        self._chain: list[Processor] = [*shared, renderer]

    def format(self, record: logging.LogRecord) -> str:
        event_dict: dict[str, Any] = {
            "event": record.getMessage(),
            "logger": record.name,
            "level": record.levelname.lower(),
        }
        if record.exc_info:
            event_dict["exc_info"] = record.exc_info
        for proc in self._chain:
            event_dict = proc(None, record.levelname.lower(), event_dict)  # type: ignore[assignment]
        return event_dict if isinstance(event_dict, str) else str(event_dict)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
