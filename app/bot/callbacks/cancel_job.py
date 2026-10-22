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

Authorization:

* ``callback_data`` is client-controlled (an MTProto client can send any
  payload for a message it can see) and ``job_id`` is a sequential DB id,
  so the handler loads the job and cancels only when ``job.user_id`` is
  the caller. Missing job, foreign job and a failed lookup all fail
  closed with the same neutral answer.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.application.services.job_cancellation import JobCancellationStore
from app.bot.callbacks.codec import CancelJobCallback
from app.bot.container import get_container
from app.domain.repositories.jobs_repo import JobsRepository
from app.logging_config import get_logger
from app.utils.correlation import bind_context, new_request_id

_logger = get_logger(__name__)

CANCEL_ACCEPTED_TEXT = "Отменяется…"
CANCEL_DENIED_TEXT = "Эту загрузку нельзя отменить."


async def _owned_by(jobs_repo: JobsRepository, *, job_id: int, user_id: int) -> bool:
    """Whether ``job_id`` exists and belongs to ``user_id``; errors deny."""
    try:
        job = await jobs_repo.get(job_id)
    except Exception:
        _logger.exception("cancel_job_lookup_failed")
        return False
    if job is None:
        _logger.info("cancel_job_denied", reason="not_found")
        return False
    if job.user_id != user_id:
        # Somebody sent a crafted ``cj|<id>`` for a job that is not theirs.
        _logger.warning("cancel_job_denied", reason="not_owner", owner_user_id=job.user_id)
        return False
    return True


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
    jobs_repo: JobsRepository | None = getattr(container, "jobs_repo", None)

    correlation_id = new_request_id()
    with bind_context(
        request_id=correlation_id,
        user_id=user.id,
        chat_id=chat.id,
        job_id=parsed.job_id,
    ):
        if cancel_store is None or reporter is None or jobs_repo is None:
            # Container misconfigured — this is a bug, not a user
            # error. Without the repo ownership cannot be checked, so
            # nothing is cancelled; still answer so the spinner stops.
            _logger.error("cancel_job_container_missing_deps")
            await query.answer(CANCEL_ACCEPTED_TEXT)
            return

        # One primary-key lookup before the ack; the spinner waits for
        # it, which is the price of telling the caller the truth.
        if not await _owned_by(jobs_repo, job_id=parsed.job_id, user_id=user.id):
            await query.answer(CANCEL_DENIED_TEXT)
            return

        # The confirmation text is intentionally short — the placeholder
        # caption itself is about to flip to "Отменено".
        await query.answer(CANCEL_ACCEPTED_TEXT)

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
