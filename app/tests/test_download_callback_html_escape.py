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
    pass


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


class _FakeContainer:
    def __init__(self, analyzed: Any) -> None:
        self.request_state = _FakeRequestState(analyzed)
        self.enqueue_download = _FakeEnqueue()


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
