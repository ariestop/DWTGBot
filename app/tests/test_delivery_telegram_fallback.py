"""When Telegram rejects the direct upload (``BadRequest`` / network
blip / etc.), ``DeliveryService`` must degrade to the temp-link path
instead of bubbling the exception up to the worker. Previously such
failures escaped as non-``AppError`` exceptions, arq retried, and the
terminal attempt surfaced the generic fallback message -- even though
the file was already on disk and deliverable via the same temp-link
branch we use unconditionally for >50 MB files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError

from app.application.services.delivery_service import DeliveryService
from app.domain.entities.media_info import DownloadResult
from app.domain.enums import DeliveryMethod, MediaKind


@dataclass
class _Call:
    kind: str
    file: Path | None
    text: str | None
    caption: str | None
    reply_markup: InlineKeyboardMarkup | None


class _SenderFailingVideo:
    """``send_video`` always blows up; other endpoints stay functional."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls: list[_Call] = []

    async def send_video(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> str | None:
        del chat_id, file_path, caption, reply_markup
        raise self._exc

    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        del chat_id
        self.calls.append(
            _Call(kind="text", file=None, text=text, caption=None, reply_markup=reply_markup)
        )


class _FakeStorage:
    def assert_under_max(self, n: int) -> None:
        del n

    def package_zip(self, files: list[Path], *, job_id: int, base_name: str) -> Path:
        raise AssertionError("not reachable in these tests")


class _FakeTempLinks:
    async def issue(self, *, job_id: int, file_path: str) -> tuple[int, str]:
        del job_id, file_path
        return (1, "https://tmp.example/abc")


def _settings() -> Any:
    return type(
        "_S",
        (),
        {
            "telegram_max_upload_bytes": 50 * 1024 * 1024,
            "max_file_size_bytes": 1024 * 1024 * 1024,
            "BRAND_FOOTER": "",
        },
    )()


def _small_video(tmp_path: Path) -> DownloadResult:
    f = tmp_path / "small.mp4"
    f.write_bytes(b"x" * 1024)
    return DownloadResult(
        files=(str(f),),
        total_size_bytes=f.stat().st_size,
        primary_mime="video/mp4",
        title="Demo",
        kind=MediaKind.VIDEO,
    )


@pytest.mark.asyncio
async def test_badrequest_on_send_video_falls_back_to_temp_link(tmp_path: Path) -> None:
    sender = _SenderFailingVideo(BadRequest("Telegram rejected this video"))
    service = DeliveryService(
        settings=_settings(),
        sender=sender,
        storage=_FakeStorage(),
        temp_links=_FakeTempLinks(),
        post_text_store=None,
    )
    result = _small_video(tmp_path)

    outcome = await service.deliver(job_id=42, chat_id=99, result=result)

    assert outcome.method is DeliveryMethod.TEMP_LINK
    assert outcome.public_url == "https://tmp.example/abc"
    # User got a text message with the link instead of hitting the
    # retry+terminal-fail path.
    assert len(sender.calls) == 1
    assert sender.calls[0].kind == "text"
    assert "tmp.example" in (sender.calls[0].text or "")


@pytest.mark.asyncio
async def test_network_error_on_send_video_falls_back_to_temp_link(tmp_path: Path) -> None:
    sender = _SenderFailingVideo(NetworkError("connection reset"))
    service = DeliveryService(
        settings=_settings(),
        sender=sender,
        storage=_FakeStorage(),
        temp_links=_FakeTempLinks(),
        post_text_store=None,
    )
    result = _small_video(tmp_path)

    outcome = await service.deliver(job_id=42, chat_id=99, result=result)

    assert outcome.method is DeliveryMethod.TEMP_LINK
