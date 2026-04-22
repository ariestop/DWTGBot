"""
Handler for the "Получить текст поста 👇" button (ADR-0010 §2.3).

Contract:

* Callback data ``pt|<job_id>``; decoded via
  :class:`app.bot.callbacks.codec.PostTextCallback`.
* Payload is pulled from Redis key ``post_text:{job_id}``. TTL is
  product-defined (``Settings.POST_TEXT_TTL_SEC``, default 24h); past
  that we show an alert instead of silently sending an empty message.
* Output is **plain text** with ``parse_mode=None``. We deliberately
  avoid a parse mode so arbitrary markup in the source (YouTube /
  Instagram embed fragments, raw ``<iframe>`` tags, stray ``*`` / ``_``
  from markdown-happy captions) displays literally and cannot forge
  entities, malformed tags, or broken links. Edge case E14.
* Web-page preview is disabled — descriptions routinely contain
  dozens of URLs and we do not want Telegram to auto-expand the first
  one into a giant preview card that dwarfs the caption.
* Long descriptions are chunked to fit below the Telegram message
  limit (4096 chars). We use 4000 as a conservative soft cap so the
  ``(n/N) `` prefix fits without hitting the hard limit. Chunking
  respects paragraph / line boundaries where possible so the user
  sees natural breaks; only pathological monoliths (no newlines for
  > 4000 chars) get hard-cut.

The handler is intentionally defensive: neither a Redis outage nor a
Telegram send failure should leave the user staring at a spinning
"часики". We always call ``answer_callback_query`` first.
"""

from __future__ import annotations

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.application.services.post_text_store import PostTextStore
from app.bot.callbacks.codec import PostTextCallback
from app.bot.container import get_container
from app.logging_config import get_logger
from app.utils.correlation import bind_context, new_request_id

_logger = get_logger(__name__)

# 4096 is the Telegram hard limit for text messages. We reserve
# ~96 chars so a ``(n/N) `` prefix and a potential trailing newline
# always fit safely. If a future translation of the prefix grows,
# adjust here first.
_CHUNK_LIMIT = 4000


def _split_chunks(text: str, *, limit: int = _CHUNK_LIMIT) -> list[str]:
    """Split ``text`` into ≤ ``limit`` slices on natural boundaries.

    Algorithm: greedy — fill a buffer to ``limit`` chars preferring
    splits at double-newline > single-newline > space > hard-cut.
    Not the most compact packer (we may leave a paragraph's worth
    of slack in a chunk to avoid splitting it) but readable output
    beats minimal message count here.
    """
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        # Prefer paragraph break, then line break, then whitespace.
        split_at = window.rfind("\n\n")
        if split_at < limit // 2:
            split_at = window.rfind("\n")
        if split_at < limit // 2:
            split_at = window.rfind(" ")
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _format_chunk(chunk: str, *, idx: int, total: int) -> str:
    """Apply the ``(n/N) `` prefix iff ``total > 1``."""
    if total <= 1:
        return chunk
    return f"({idx}/{total}) {chunk}"


async def handle_post_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return

    parsed = PostTextCallback.try_decode(query.data)
    if parsed is None:
        # Not our callback — another handler will pick it up.
        return

    container = get_container(context.bot_data)
    store: PostTextStore | None = getattr(container, "post_text_store", None)

    correlation_id = new_request_id()
    with bind_context(
        request_id=correlation_id,
        user_id=user.id,
        chat_id=chat.id,
        job_id=parsed.job_id,
    ):
        if store is None:
            # Container misconfigured — surface a polite alert and log
            # loud. Better than a silent timeout on the client.
            _logger.error("post_text_container_missing_store")
            try:
                await query.answer(
                    "Текст поста больше недоступен",
                    show_alert=True,
                )
            except TelegramError:
                _logger.exception("post_text_answer_failed")
            return

        text: str | None
        try:
            text = await store.get(job_id=parsed.job_id)
        except Exception:  # pragma: no cover  store swallows Redis errors
            _logger.exception("post_text_store_get_failed")
            text = None

        if not text:
            try:
                await query.answer(
                    "Текст поста больше недоступен",
                    show_alert=True,
                )
            except TelegramError:
                _logger.exception("post_text_answer_failed")
            _logger.info("post_text_callback", result="expired")
            return

        # Stop the spinner right away; the chunked send below can take
        # a few hundred ms for very long descriptions.
        try:
            await query.answer()
        except TelegramError:
            _logger.exception("post_text_answer_failed")

        chunks = _split_chunks(text)
        total = len(chunks)
        sent = 0
        for idx, chunk in enumerate(chunks, start=1):
            body = _format_chunk(chunk, idx=idx, total=total)
            try:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=body,
                    disable_web_page_preview=True,
                )
                sent += 1
            except TelegramError:
                _logger.exception(
                    "post_text_send_failed",
                    chunk_index=idx,
                    chunks_total=total,
                )
                # Abort on first failure; partial delivery is noise.
                break

        _logger.info(
            "post_text_callback",
            result="served" if sent == total else "partial",
            chunks_total=total,
            chunks_sent=sent,
        )
