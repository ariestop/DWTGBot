"""Audit fix A6: download callback escapes ``selected.label`` before
interpolation so that a flip to ``parse_mode="HTML"`` (or adoption of
``Defaults(parse_mode=ParseMode.HTML)`` in the Application builder)
does not immediately open an HTML-injection sink.

The production label stream is whitelist-safe today, so we use an
intentionally hostile synthetic label and verify the text that would be
shipped to Telegram carries entity references, not raw tags.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

import app.bot.callbacks.download as download_cb
from app.bot.callbacks.codec import DownloadCallback


@dataclass
class _CapturedEdit:
    text: str


class _FakeMessage:
    def __init__(self, message_id: int = 42) -> None:
        self.message_id = message_id


class _FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = _FakeMessage()
        self.edits: list[_CapturedEdit] = []
        self.answers: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        self.answers.append(text)

    async def edit_message_text(self, text: str, *_: Any, **__: Any) -> None:
        self.edits.append(_CapturedEdit(text=text))


class _FakeRequestState:
    def __init__(self, analyzed: Any) -> None:
        self._analyzed = analyzed

    async def load(self, _request_id: str) -> Any:
        return self._analyzed

    async def delete(self, _request_id: str) -> None:
        return None


class _FakeEnqueue:
    async def execute(self, _payload: Any) -> Any:
        return SimpleNamespace(job_id=999)


class _FakeProgressReporter:
    """Records ``start`` calls so tests can assert wiring without Redis.

    Kept permissive about ``update`` / ``finish`` / ``fail`` / ``cancel``
    because this fixture is reused by tests that only care about the
    edit-text surface — those methods just need to exist as no-ops.
    """

    def __init__(self) -> None:
        self.start_calls: list[dict[str, Any]] = []

    async def start(
        self,
        *,
        job_id: int,
        chat_id: int,
        message_id: int,
        thumbnail_url: str | None,
    ) -> None:
        self.start_calls.append(
            {
                "job_id": job_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "thumbnail_url": thumbnail_url,
            }
        )

    async def update(self, **_: Any) -> None:
        return None

    async def finish(self, **_: Any) -> None:
        return None

    async def fail(self, **_: Any) -> None:
        return None

    async def cancel(self, **_: Any) -> None:
        return None


class _FakeContainer:
    def __init__(self, analyzed: Any) -> None:
        self.request_state = _FakeRequestState(analyzed)
        self.enqueue_download = _FakeEnqueue()
        self.progress_reporter = _FakeProgressReporter()


@pytest.mark.asyncio
async def test_selected_label_is_html_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    malicious = '<b onclick="alert(1)">pwned</b>'
    option = SimpleNamespace(key="video_720", label=malicious)
    analyzed = SimpleNamespace(
        options=[option],
        info=SimpleNamespace(platform="youtube", raw={"source_url": "https://youtu.be/abc"}),
    )
    container = _FakeContainer(analyzed)

    monkeypatch.setattr(download_cb, "get_container", lambda _bd: container)

    raw = DownloadCallback(request_id="rq", option_key="video_720").encode()
    query = _FakeQuery(data=raw)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=2),
    )
    context = SimpleNamespace(bot_data={})

    await download_cb.handle_download_callback(update, context)  # type: ignore[arg-type]

    assert query.edits, "expected the placeholder message to be edited"
    sent = query.edits[-1].text
    assert malicious not in sent
    assert "&lt;b" in sent
    assert "&quot;" in sent or "&#x27;" in sent or "onclick" in sent
    # Sanity: the human-facing surround text is preserved.
    assert "Скачиваю" in sent


@pytest.mark.asyncio
async def test_picker_registers_progress_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard for the YouTube "no progress bar" bug.

    The legacy picker flow used to enqueue a job without calling
    ``ProgressReporter.start``, so ``progress_updater`` had no
    ``progress_meta:{job_id}`` to edit and the bar never appeared on
    YouTube downloads. Verify the callback now registers the edited
    placeholder message as the progress target — and with
    ``thumbnail_url=None`` so the updater uses ``edit_message_text``
    (the picker placeholder is a plain text message, not a photo).
    """
    option = SimpleNamespace(key="video_1080", label="Видео 1080p")
    analyzed = SimpleNamespace(
        options=[option],
        info=SimpleNamespace(
            platform="youtube",
            raw={"source_url": "https://youtu.be/abc"},
            thumbnail_url="https://i.ytimg.com/vi/abc/hqdefault.jpg",
        ),
    )
    container = _FakeContainer(analyzed)

    monkeypatch.setattr(download_cb, "get_container", lambda _bd: container)

    raw = DownloadCallback(request_id="rq", option_key="video_1080").encode()
    query = _FakeQuery(data=raw)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=2),
    )
    context = SimpleNamespace(bot_data={})

    await download_cb.handle_download_callback(update, context)  # type: ignore[arg-type]

    starts = container.progress_reporter.start_calls
    assert len(starts) == 1, "expected exactly one progress_reporter.start call"
    call = starts[0]
    assert call["job_id"] == 999
    assert call["chat_id"] == 2
    assert call["message_id"] == query.message.message_id
    # Picker placeholder is a text message — passing a thumbnail_url
    # here would make _ActiveJob.from_meta pick the caption edit path
    # and every subsequent edit would 400 at Telegram.
    assert call["thumbnail_url"] is None
