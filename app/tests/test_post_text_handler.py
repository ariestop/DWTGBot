"""Post-text button handler — ADR-0010 §2.3.

Covers the happy path (served), TTL expiry (alert), long-text chunking
with ``(n/N)`` prefix, telegram send failures, container misconfig,
and Redis read errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from telegram.error import TelegramError

from app.bot.callbacks import post_text as post_text_mod
from app.bot.callbacks.codec import PostTextCallback
from app.bot.callbacks.post_text import (
    _CHUNK_LIMIT,
    _split_chunks,
    handle_post_text_callback,
)


@pytest.fixture(autouse=True)
def _patch_get_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the ``isinstance(container, BotContainer)`` guard in
    ``get_container``. Constructing a real ``BotContainer`` here would
    drag in ~15 infra dependencies for a handler that only reads one
    attribute. ``SimpleNamespace`` is the canonical duck type."""
    monkeypatch.setattr(
        post_text_mod,
        "get_container",
        lambda bot_data: bot_data["__test_container__"],
    )


class _FakeStore:
    def __init__(self, texts: dict[int, str | None]) -> None:
        self._texts = texts
        self.get_calls: list[int] = []

    async def put(self, *, job_id: int, text: str) -> None:
        self._texts[job_id] = text

    async def get(self, *, job_id: int) -> str | None:
        self.get_calls.append(job_id)
        return self._texts.get(job_id)

    async def exists(self, *, job_id: int) -> bool:
        return self._texts.get(job_id) is not None


class _BoomStore(_FakeStore):
    async def get(self, *, job_id: int) -> str | None:  # type: ignore[override]
        self.get_calls.append(job_id)
        raise RuntimeError("redis is sad")


@dataclass
class _FakeQuery:
    data: str
    answers: list[tuple[str | None, bool]] = field(default_factory=list)

    async def answer(self, text: str | None = None, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


@dataclass
class _SentMessage:
    chat_id: int
    text: str
    disable_web_page_preview: bool | None
    parse_mode: Any


class _FakeBot:
    def __init__(self, *, raise_on_send: int | None = None) -> None:
        self.sent: list[_SentMessage] = []
        self._raise_on_send = raise_on_send

    async def send_message(self, **kwargs: Any) -> None:
        if self._raise_on_send is not None and len(self.sent) == self._raise_on_send:
            raise TelegramError("network glitch")
        self.sent.append(
            _SentMessage(
                chat_id=kwargs["chat_id"],
                text=kwargs["text"],
                disable_web_page_preview=kwargs.get("disable_web_page_preview"),
                parse_mode=kwargs.get("parse_mode"),
            )
        )


def _make_update(callback_data: str) -> tuple[SimpleNamespace, _FakeQuery]:
    query = _FakeQuery(data=callback_data)
    user = SimpleNamespace(id=7)
    chat = SimpleNamespace(id=11)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=user,
        effective_chat=chat,
    )
    return update, query  # type: ignore[return-value]


def _make_context(*, store, bot) -> SimpleNamespace:
    container = SimpleNamespace(post_text_store=store)
    return SimpleNamespace(bot_data={"__test_container__": container}, bot=bot)


class TestSplitChunks:
    def test_short_text_single_chunk(self) -> None:
        assert _split_chunks("hello") == ["hello"]

    def test_empty_text_returns_empty_list(self) -> None:
        assert _split_chunks("") == []

    def test_long_text_splits_on_paragraph_break(self) -> None:
        para_a = "A" * 2000
        para_b = "B" * 2500
        text = f"{para_a}\n\n{para_b}"
        chunks = _split_chunks(text, limit=3000)
        assert len(chunks) == 2
        assert chunks[0].startswith("AAA")
        assert chunks[1].startswith("BBB")

    def test_long_text_falls_back_to_word_split(self) -> None:
        # No paragraph break; rely on space splits.
        words = ["alpha"] * 1500
        text = " ".join(words)
        chunks = _split_chunks(text, limit=2000)
        assert len(chunks) >= 2
        # Each chunk stays under the limit after the rstrip.
        assert all(len(c) <= 2000 for c in chunks)

    def test_pathological_input_hard_cuts(self) -> None:
        text = "Z" * 10_000
        chunks = _split_chunks(text, limit=4000)
        assert len(chunks) == 3
        # All full chunks fit exactly to the limit; the tail takes the
        # remainder. ``rstrip`` is a no-op for pure content.
        assert len(chunks[0]) == 4000
        assert len(chunks[1]) == 4000
        assert len(chunks[2]) == 2000


class TestHandler:
    @pytest.mark.asyncio
    async def test_happy_path_sends_single_chunk(self) -> None:
        store = _FakeStore({42: "hello world"})
        bot = _FakeBot()
        update, query = _make_update(PostTextCallback(job_id=42).encode())
        ctx = _make_context(store=store, bot=bot)

        await handle_post_text_callback(update, ctx)

        assert len(bot.sent) == 1
        sent = bot.sent[0]
        assert sent.chat_id == 11
        # No ``(n/N)`` prefix for single-chunk output.
        assert sent.text == "hello world"
        # Explicitly opted out of web previews and any parse_mode; the
        # source text may contain arbitrary markup and we show it as
        # literal text so Telegram cannot mis-parse it.
        assert sent.disable_web_page_preview is True
        assert sent.parse_mode is None
        # ``answer()`` with no args after the ack so the spinner clears.
        assert query.answers == [(None, False)]

    @pytest.mark.asyncio
    async def test_missing_text_triggers_alert(self) -> None:
        store = _FakeStore({42: None})
        bot = _FakeBot()
        update, query = _make_update(PostTextCallback(job_id=42).encode())
        ctx = _make_context(store=store, bot=bot)

        await handle_post_text_callback(update, ctx)

        assert bot.sent == []
        assert query.answers == [("Текст поста больше недоступен", True)]

    @pytest.mark.asyncio
    async def test_non_positive_callback_is_silently_ignored(self) -> None:
        # Wrong prefix — handler returns without touching the store.
        store = _FakeStore({42: "hi"})
        bot = _FakeBot()
        update, query = _make_update("cj|42")
        ctx = _make_context(store=store, bot=bot)

        await handle_post_text_callback(update, ctx)

        assert bot.sent == []
        assert query.answers == []
        assert store.get_calls == []

    @pytest.mark.asyncio
    async def test_long_text_splits_with_numbered_prefix(self) -> None:
        text = "X" * (_CHUNK_LIMIT * 2 + 500)
        store = _FakeStore({42: text})
        bot = _FakeBot()
        update, _query = _make_update(PostTextCallback(job_id=42).encode())
        ctx = _make_context(store=store, bot=bot)

        await handle_post_text_callback(update, ctx)

        assert len(bot.sent) == 3
        assert bot.sent[0].text.startswith("(1/3) ")
        assert bot.sent[1].text.startswith("(2/3) ")
        assert bot.sent[2].text.startswith("(3/3) ")

    @pytest.mark.asyncio
    async def test_send_failure_halts_partial_delivery(self) -> None:
        # Simulate a Telegram rate-limit on the second chunk — we abort
        # delivery rather than producing a gap.
        text = "X" * (_CHUNK_LIMIT * 2 + 500)
        store = _FakeStore({42: text})
        bot = _FakeBot(raise_on_send=1)
        update, _query = _make_update(PostTextCallback(job_id=42).encode())
        ctx = _make_context(store=store, bot=bot)

        await handle_post_text_callback(update, ctx)

        # Only the first chunk makes it; the second raises and loop
        # breaks before the third attempt.
        assert len(bot.sent) == 1
        assert bot.sent[0].text.startswith("(1/3) ")

    @pytest.mark.asyncio
    async def test_store_read_error_degrades_to_alert(self) -> None:
        store = _BoomStore({42: "hi"})
        bot = _FakeBot()
        update, query = _make_update(PostTextCallback(job_id=42).encode())
        ctx = _make_context(store=store, bot=bot)

        await handle_post_text_callback(update, ctx)

        assert bot.sent == []
        assert query.answers == [("Текст поста больше недоступен", True)]

    @pytest.mark.asyncio
    async def test_missing_store_returns_alert(self) -> None:
        # Container misconfig — simulate a stale build that forgot to
        # wire the store. Handler must not leave the spinner spinning.
        bot = _FakeBot()
        update, query = _make_update(PostTextCallback(job_id=42).encode())
        container = SimpleNamespace(post_text_store=None)
        ctx = SimpleNamespace(bot_data={"__test_container__": container}, bot=bot)

        await handle_post_text_callback(update, ctx)

        assert bot.sent == []
        assert query.answers == [("Текст поста больше недоступен", True)]
