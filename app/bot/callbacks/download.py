"""Callback-query handler for download option buttons."""

from __future__ import annotations

import html

from telegram import Update
from telegram.ext import ContextTypes

from app.application.dto.jobs import EnqueueDownloadInput
from app.application.dto.media import AnalyzedMedia
from app.bot.callbacks.codec import PREFIX_CANCEL, SEP, DownloadCallback
from app.bot.container import get_container
from app.exceptions import AppError
from app.logging_config import get_logger
from app.utils.correlation import bind_context, new_request_id

_logger = get_logger(__name__)


async def handle_download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return

    raw = query.data

    if raw.startswith(PREFIX_CANCEL + SEP):
        await query.answer("Отменено")
        if query.message is not None:
            await query.edit_message_text("Отменено.")
        return

    parsed = DownloadCallback.try_decode(raw)
    if parsed is None:
        await query.answer("Неизвестное действие")
        return

    container = get_container(context.bot_data)
    analyzed = await container.request_state.load(parsed.request_id)

    if analyzed is None:
        await query.answer("Эта кнопка устарела", show_alert=True)
        if query.message is not None:
            await query.edit_message_text("Сессия истекла. Пришлите ссылку ещё раз.")
        return

    selected = next((o for o in analyzed.options if o.key == parsed.option_key), None)
    if selected is None:
        await query.answer("Этот вариант больше недоступен", show_alert=True)
        return

    correlation_id = new_request_id()

    with bind_context(
        request_id=correlation_id,
        user_id=user.id,
        chat_id=chat.id,
        option_key=selected.key,
    ):
        await query.answer("Принято")
        try:
            result = await container.enqueue_download.execute(
                EnqueueDownloadInput(
                    user_id=user.id,
                    chat_id=chat.id,
                    source_url=_resolve_source_url(analyzed),
                    platform=analyzed.info.platform,
                    selected_option_key=selected.key,
                    correlation_id=correlation_id,
                )
            )
        except AppError as exc:
            _logger.warning("enqueue_failed", error=str(exc))
            if query.message is not None:
                await query.edit_message_text(exc.user_message)
            return
        except Exception as exc:
            _logger.exception("enqueue_unexpected_error", error=str(exc))
            if query.message is not None:
                await query.edit_message_text("Не удалось поставить задачу в очередь.")
            return

        await container.request_state.delete(parsed.request_id)
        if query.message is not None:
            # Audit fix A6: defense-in-depth — ``selected.label`` is
            # assembled by provider logic today and is currently
            # whitelist-safe ("Видео 720p", "Фото", etc.), but any
            # future change that flows user- or upstream-supplied
            # strings into the label (e.g. source-side track title)
            # would become an HTML injection sink the moment this
            # call is flipped to ``parse_mode="HTML"`` — which is
            # already the default for sibling bot handlers
            # (``handlers/links.py``). Escape at the sink.
            safe_label = html.escape(selected.label)
            await query.edit_message_text(
                f"⏳ Скачиваю «{safe_label}». Это может занять немного времени.",
            )
            # Register the (now edited) picker message as the progress
            # placeholder so ``ProgressUpdater`` flips its text live as
            # yt-dlp emits percent events — same surface the instant
            # flow gets from ``AutoEnqueueDownloadUseCase``. The legacy
            # picker path (YouTube, and any platform re-added to
            # ``picker_platforms`` in ``handle_link``) used to skip
            # this wiring and therefore showed no progress bar at all.
            #
            # We intentionally pass ``thumbnail_url=None``: the picker
            # placeholder is a plain text message, so the updater must
            # call ``edit_message_text`` (not ``edit_message_caption``)
            # — ``_ActiveJob.from_meta`` drives that choice off the
            # presence/absence of ``thumbnail_url`` in the meta hash.
            #
            # Reporter failures are swallowed by the reporter itself
            # (ADR-0010 §2.2): we never let a broken progress channel
            # undo an enqueue that already succeeded.
            try:
                await container.progress_reporter.start(
                    job_id=result.job_id,
                    chat_id=chat.id,
                    message_id=query.message.message_id,
                    thumbnail_url=None,
                )
            except Exception:  # pragma: no cover  defensive
                _logger.exception(
                    "picker_progress_start_failed",
                    job_id=result.job_id,
                )
        # ADR-0010 §2.1 task §4 item 4: the internal job id is no
        # longer surfaced to the user — nothing actionable they can
        # do with it and it clutters the placeholder. ``result.job_id``
        # is still logged above for operator triage.
        del result


def _resolve_source_url(analyzed: AnalyzedMedia) -> str:
    """
    The provider stores the original URL inside MediaInfo.raw under one of the
    well-known keys. Fall back across them to be tolerant to minor differences.
    """
    raw = analyzed.info.raw or {}
    for key in ("source_url", "webpage_url", "original_url"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return ""
