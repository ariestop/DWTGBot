"""Worker-side ``TelegramSender``.

A fake ``telegram.Bot`` records the kwargs of every call, so the tests
check what we actually hand to Telegram: parse mode, link preview,
upload timeouts, probed video dimensions and returned ``file_id``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telegram import InputFile
from telegram.constants import ParseMode

from app.config import get_settings
from app.infrastructure.telegram import sender as sender_mod
from app.infrastructure.telegram.sender import TelegramSender


class _FakeBot:
    def __init__(self, **_: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.reply: Any = SimpleNamespace(video=None, audio=None, photo=[], document=None)

    async def _record(self, method: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((method, kwargs))
        return self.reply

    async def send_message(self, **kwargs: Any) -> Any:
        return await self._record("send_message", kwargs)

    async def send_video(self, **kwargs: Any) -> Any:
        return await self._record("send_video", kwargs)

    async def send_audio(self, **kwargs: Any) -> Any:
        return await self._record("send_audio", kwargs)

    async def send_photo(self, **kwargs: Any) -> Any:
        return await self._record("send_photo", kwargs)

    async def send_document(self, **kwargs: Any) -> Any:
        return await self._record("send_document", kwargs)


@pytest.fixture
def bot(monkeypatch: pytest.MonkeyPatch) -> _FakeBot:
    fake = _FakeBot()
    monkeypatch.setattr(sender_mod, "Bot", lambda **_kw: fake)
    return fake


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00\x01payload")
    return path


async def test_send_text_uses_html_and_explicit_link_preview_options(bot: _FakeBot) -> None:
    sender = TelegramSender(get_settings())

    await sender.send_text(123, "<b>hi</b>", disable_web_page_preview=True)

    method, kwargs = bot.calls[0]
    assert method == "send_message"
    assert kwargs["chat_id"] == 123
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwargs["link_preview_options"].is_disabled is True


async def test_send_video_passes_probed_dimensions_and_returns_file_id(
    bot: _FakeBot, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_probe(_path: Path) -> Any:
        return sender_mod._VideoMeta(width=720, height=1280, duration=15)

    monkeypatch.setattr(sender_mod, "_probe_video", fake_probe)
    bot.reply = SimpleNamespace(video=SimpleNamespace(file_id="vid-1"))
    sender = TelegramSender(get_settings())

    file_id = await sender.send_video(1, media_file, caption="cap")

    _, kwargs = bot.calls[0]
    assert file_id == "vid-1"
    assert (kwargs["width"], kwargs["height"], kwargs["duration"]) == (720, 1280, 15)
    assert kwargs["supports_streaming"] is True
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwargs["write_timeout"] == sender_mod._UPLOAD_TIMEOUT_S
    assert isinstance(kwargs["video"], InputFile)
    assert kwargs["video"].filename == "clip.mp4"


async def test_send_video_without_caption_sends_no_parse_mode(
    bot: _FakeBot, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sender_mod.shutil, "which", lambda _name: None)
    sender = TelegramSender(get_settings())

    file_id = await sender.send_video(1, media_file)

    _, kwargs = bot.calls[0]
    assert file_id is None
    assert kwargs["parse_mode"] is None
    assert (kwargs["width"], kwargs["height"], kwargs["duration"]) == (None, None, None)


async def test_send_photo_returns_largest_size_file_id(bot: _FakeBot, media_file: Path) -> None:
    bot.reply = SimpleNamespace(
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")]
    )
    sender = TelegramSender(get_settings())

    assert await sender.send_photo(1, media_file) == "large"


async def test_send_audio_and_document_return_file_ids(bot: _FakeBot, media_file: Path) -> None:
    bot.reply = SimpleNamespace(
        audio=SimpleNamespace(file_id="aud-1"),
        document=SimpleNamespace(file_id="doc-1"),
    )
    sender = TelegramSender(get_settings())

    assert await sender.send_audio(1, media_file) == "aud-1"
    assert await sender.send_document(1, media_file) == "doc-1"
    assert [method for method, _ in bot.calls] == ["send_audio", "send_document"]


async def test_probe_video_without_ffprobe_returns_unknown_meta(
    media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sender_mod.shutil, "which", lambda _name: None)

    meta = await sender_mod._probe_video(media_file)

    assert (meta.width, meta.height, meta.duration) == (None, None, None)
