"""
Handler for the live-progress cancel button (ADR-0010 §2.1).

Contract:

* Cancel is **cooperative**. We do NOT interrupt yt-dlp / ffmpeg
  mid-stream — that would require signal plumbing and a safe kill
  window for the ffmpeg subprocess. Instead we write a Redis flag
  and let the worker notice it at the next phase boundary.
* The bot immediately publishes a terminal ``CANCELLED`` event on the
  progress channel so the placeholder caption flips to "Отменено"
  right away. The worker will mark the DB row FAILED with a
  cancelled-by-user reason when it next polls the flag; those two
  actions are deliberately decoupled so the user always sees
  instant feedback.

Limitations (MVP):

* Cancel is effective only in ``ANALYZING`` / ``DOWNLOADING`` phases.
  Once the worker reaches ``PROCESSING`` (ffmpeg) the job will run to
  completion — the user gets the video despite having tapped cancel.
  That is documented as the MVP trade-off in ``docs/tasks/
  archive/instant-download-ux.md`` Edge case E6 / Risk R9.
* Double-tap: the Telegram client shows a tiny spinner and sends the
  same callback twice. The second one finds no matching state; we
  still answer politely so the spinner stops.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.application.services.job_cancellation import JobCancellationStore
from app.bot.callbacks.codec import CancelJobCallback
from app.bot.container import get_container
from app.logging_config import get_logger
from app.utils.correlation import bind_context, new_request_id

_logger = get_logger(__name__)


async def handle_cancel_job_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return

    parsed = CancelJobCallback.try_decode(query.data)
    if parsed is None:
        # Not our callback — another handler will pick it up.
        return

    container = get_container(context.bot_data)
    cancel_store: JobCancellationStore | None = getattr(container, "job_cancellation", None)
    reporter = getattr(container, "progress_reporter", None)

    correlation_id = new_request_id()
    with bind_context(
        request_id=correlation_id,
        user_id=user.id,
        chat_id=chat.id,
        job_id=parsed.job_id,
    ):
        # Ack *first* so the Telegram spinner stops even if Redis is
        # slow. The confirmation text is intentionally short — the
        # placeholder caption itself is about to flip to "Отменено".
        await query.answer("Отменяется…")

        if cancel_store is None or reporter is None:
            # Container misconfigured — this is a bug, not a user
            # error. Log and stay quiet; the placeholder will time
            # out via the watchdog if truly abandoned.
            _logger.error("cancel_job_container_missing_deps")
            return

        try:
            await cancel_store.request(job_id=parsed.job_id)
        except Exception:
            _logger.exception("cancel_job_flag_set_failed")
            # Continue to reporter.cancel anyway — the user sees
            # immediate UI feedback and the worker will just finish
            # the job normally.
        try:
            await reporter.cancel(job_id=parsed.job_id)
        except Exception:  # pragma: no cover  reporter swallows Redis errors
            _logger.exception("cancel_job_reporter_failed")

        _logger.info("cancel_job_requested", job_id=parsed.job_id)
