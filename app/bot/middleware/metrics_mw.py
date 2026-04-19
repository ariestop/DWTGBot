"""Per-handler instrumentation: latency + outcome classification.

PTB has no built-in post-handler middleware (the existing
:func:`bind_request_context` runs *before* handlers). To emit the
``bot_message_handled`` event with end-to-end latency we wrap each
registered handler with :func:`instrument`.

The decorator is intentionally tiny and side-effect-free at import
time so unit tests can ``from app.bot.middleware.metrics_mw import
instrument`` without bringing in PTB heavy machinery.

Errors are *re-raised* — PTB's ``on_error`` is the canonical place to
react to handler failures (sends a user message, etc.). We only
classify and observe.

See ``ADR-0007`` §2.1 (table row ``bot_message_handled_total``) and
``docs/35-`` §3.1 A1 for why latency is bucketed rather than measured
as a Histogram (cardinality is bounded → fast PromQL).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.container import get_container
from app.domain.observability import HandlerOutcome, latency_bucket
from app.domain.reason_class import ReasonClass, classify_exception
from app.exceptions import AppError
from app.logging_config import get_logger

_logger = get_logger(__name__)

HandlerFn = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[object]]


def instrument(handler: HandlerFn) -> HandlerFn:
    """Wrap a PTB handler with latency + outcome instrumentation.

    The wrapper:

    1. Times the handler with a monotonic clock (immune to wall-clock
       jumps from NTP).
    2. On success, classifies as ``ok`` (or ``user_error`` if the
       handler signalled it via ``context.user_data['_handler_outcome']``
       — used by handlers that *successfully* told the user "no" without
       raising, e.g. rate-limit denials).
    3. On ``AppError``, classifies via :func:`classify_exception`. If
       the resulting ``ReasonClass`` is ``user_error``, the outcome is
       ``HandlerOutcome.USER_ERROR`` (the bot stayed responsive — A1
       still counts as served).
    4. On any other exception, the outcome is ``internal_error``. Always
       re-raises so PTB's ``on_error`` runs.
    """

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> object:
        container = get_container(context.bot_data)
        metrics = container.job_metrics
        start = time.monotonic()
        outcome = HandlerOutcome.OK
        reason_for_log: ReasonClass | None = None
        try:
            result = await handler(update, context)
        except AppError as exc:
            reason = classify_exception(exc)
            outcome = (
                HandlerOutcome.USER_ERROR
                if reason is ReasonClass.USER_ERROR
                else HandlerOutcome.INTERNAL_ERROR
            )
            reason_for_log = reason
            raise
        except Exception:
            outcome = HandlerOutcome.INTERNAL_ERROR
            reason_for_log = ReasonClass.INTERNAL_ERROR
            raise
        else:
            # Handlers can downgrade their own outcome to ``user_error``
            # (e.g. ``handle_link`` returning early on a rate-limit
            # deny). Defensive: only respect the hint if it parses.
            hint = (context.user_data or {}).get("_handler_outcome")
            if isinstance(hint, HandlerOutcome):
                outcome = hint
            return result
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            bucket = latency_bucket(elapsed_ms)
            metrics.inc_bot_message_handled(outcome=outcome, latency_bucket=bucket)
            _logger.info(
                "bot_message_handled",
                outcome=outcome.value,
                latency_bucket=bucket.value,
                latency_ms=round(elapsed_ms, 1),
                reason_class=reason_for_log.value if reason_for_log else None,
                handler=handler.__name__,
            )

    wrapper.__name__ = f"instrumented_{handler.__name__}"
    wrapper.__qualname__ = wrapper.__name__
    wrapper.__doc__ = handler.__doc__
    return wrapper
