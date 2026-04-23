"""`handle_link` instant-download flag routing.

Regression guard for the roadmap contract:

* `INSTANT_DOWNLOAD_ENABLED=true` routes supported providers, including
  YouTube, through the instant auto-enqueue path.
* `INSTANT_DOWNLOAD_ENABLED=false` restores the legacy picker flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

import app.bot.handlers.links as links_mod
from app.application.dto.media import AnalyzedMedia
from app.domain.entities.media_info import DownloadOption, MediaInfo
from app.domain.enums import MediaKind, Platform


class _FakeAnalyzeLink:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def execute(self, _text: str) -> Any:
        return self._result


class _FakeBot:
    def __init__(self) -> None:
        self.chat_actions: list[tuple[int, str]] = []

    async def send_chat_action(self, *, chat_id: int, action: str) -> None:
        self.chat_actions.append((chat_id, action))


@dataclass
class _ReplyCall:
    text: str
    parse_mode: str | None
    reply_markup: object | None


@dataclass
class _FakeMessage:
    text: str
    replies: list[_ReplyCall] = field(default_factory=list)

    async def reply_text(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: object | None = None,
    ) -> None:
        self.replies.append(_ReplyCall(text=text, parse_mode=parse_mode, reply_markup=reply_markup))


def _make_analyzed(platform: Platform) -> AnalyzedMedia:
    info = MediaInfo(
        platform=platform,
        media_id="abc123",
        title="Demo",
        kind=MediaKind.VIDEO,
        thumbnail_url="https://example.com/thumb.jpg",
        raw={"source_url": "https://example.com/post"},
    )
    option = DownloadOption(
        key="video_720",
        label="Видео 720p",
        kind=MediaKind.VIDEO,
        container="mp4",
    )
    return AnalyzedMedia(info=info, options=(option,))


async def _allow_rate_limit(**_: Any) -> Any:
    return SimpleNamespace(allowed=True)


@pytest.mark.asyncio
async def test_youtube_uses_instant_flow_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzed = _make_analyzed(Platform.YOUTUBE)
    container = SimpleNamespace(
        settings=SimpleNamespace(INSTANT_DOWNLOAD_ENABLED=True, rate_limit_windows=()),
        analyze_link=_FakeAnalyzeLink(SimpleNamespace(analyzed=analyzed, request_id="rq")),
        rate_limit_gate=object(),
        metrics=object(),
        notice_throttle=object(),
    )
    message = _FakeMessage(text="https://youtu.be/abc123")
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=2),
    )
    context = SimpleNamespace(bot=_FakeBot(), bot_data={})

    calls: list[dict[str, Any]] = []

    async def _record_instant(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(links_mod, "get_container", lambda _bd: container)
    monkeypatch.setattr(links_mod, "evaluate_rate_limit", _allow_rate_limit)
    monkeypatch.setattr(links_mod, "_handle_instant_download", _record_instant)

    def _fail_keyboard(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("legacy picker must not be used when instant flow is enabled")

    monkeypatch.setattr(links_mod, "build_options_keyboard", _fail_keyboard)

    await links_mod.handle_link(update, context)  # type: ignore[arg-type]

    assert len(calls) == 1
    assert calls[0]["analyzed"] == analyzed
    assert calls[0]["chat_id"] == 2
    assert message.replies == []


@pytest.mark.asyncio
async def test_youtube_uses_picker_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzed = _make_analyzed(Platform.YOUTUBE)
    container = SimpleNamespace(
        settings=SimpleNamespace(INSTANT_DOWNLOAD_ENABLED=False, rate_limit_windows=()),
        analyze_link=_FakeAnalyzeLink(SimpleNamespace(analyzed=analyzed, request_id="rq")),
        rate_limit_gate=object(),
        metrics=object(),
        notice_throttle=object(),
    )
    message = _FakeMessage(text="https://youtu.be/abc123")
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=2),
    )
    context = SimpleNamespace(bot=_FakeBot(), bot_data={})

    async def _fail_instant(**_kwargs: Any) -> None:
        raise AssertionError("instant flow must stay off when the feature flag is disabled")

    monkeypatch.setattr(links_mod, "get_container", lambda _bd: container)
    monkeypatch.setattr(links_mod, "evaluate_rate_limit", _allow_rate_limit)
    monkeypatch.setattr(links_mod, "_handle_instant_download", _fail_instant)
    monkeypatch.setattr(links_mod, "build_options_keyboard", lambda *_a, **_kw: "KEYBOARD")

    await links_mod.handle_link(update, context)  # type: ignore[arg-type]

    assert len(message.replies) == 1
    reply = message.replies[0]
    assert "Выберите вариант:" in reply.text
    assert reply.parse_mode == "HTML"
    assert reply.reply_markup == "KEYBOARD"
