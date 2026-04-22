"""Direct-upload retry contract for files within the 49 MB window.

Product rule (explicit user requirement, 2026-04):

* files <= ``telegram_max_upload_bytes``      → always direct upload,
  never a temp link, even across transient Telegram hiccups;
* files >  ``telegram_max_upload_bytes``      → temp link, unchanged.

``DeliveryService`` therefore:

* retries ``send_*`` up to ``_UPLOAD_RETRY_ATTEMPTS`` times when the
  first attempt fails with a ``TelegramError`` (covers ``TimedOut``,
  ``NetworkError``, transient ``BadRequest`` from the CDN layer);
* propagates the last exception when the budget is exhausted, letting
  arq retry the whole job — no silent fallback to a temp link;
* leaves the >49 MB branch untouched (already goes straight to the
  link path without ever calling ``send_video``).

The previous "any TelegramError → temp link" fallback contradicted the
product rule: a 33 MB file that flaked once on upload was surfacing as
"Файл слишком большой для Telegram (33.8 MB)" even though it was
perfectly within the direct-upload window. These tests nail the new
behaviour so a regression would fail CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut

import app.application.services.delivery_service as delivery_service_module
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


class _SenderVideo:
    """Configurable sender.

    ``outcomes`` is consumed left-to-right on each ``send_video`` call.
    A ``BaseException`` is raised, anything else (including ``None``)
    is returned verbatim as the resulting ``file_id``.
    """

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[_Call] = []

    async def send_video(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> str | None:
        self.calls.append(
            _Call(
                kind="video",
                file=file_path,
                text=None,
                caption=caption,
                reply_markup=reply_markup,
            )
        )
        del chat_id
        if not self._outcomes:
            raise AssertionError("unexpected send_video call: no outcomes left")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]

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
    def __init__(self) -> None:
        self.issued: list[tuple[int, str]] = []

    async def issue(self, *, job_id: int, file_path: str) -> tuple[int, str]:
        self.issued.append((job_id, file_path))
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


def _small_video(tmp_path: Path, *, size_bytes: int = 1024) -> DownloadResult:
    f = tmp_path / "small.mp4"
    f.write_bytes(b"x" * size_bytes)
    return DownloadResult(
        files=(str(f),),
        total_size_bytes=f.stat().st_size,
        primary_mime="video/mp4",
        title="Demo",
        kind=MediaKind.VIDEO,
    )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the test suite fast: retry backoff is fixed at 3 s in prod."""

    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr(delivery_service_module.asyncio, "sleep", _instant)


@pytest.mark.asyncio
async def test_transient_timeout_retries_then_delivers_directly(tmp_path: Path) -> None:
    """First attempt trips ``TimedOut``; retry succeeds. No temp link."""
    sender = _SenderVideo([TimedOut("upload stalled"), "file-id-ok"])
    temp_links = _FakeTempLinks()
    service = DeliveryService(
        settings=_settings(),
        sender=sender,
        storage=_FakeStorage(),
        temp_links=temp_links,
        post_text_store=None,
    )
    result = _small_video(tmp_path)

    outcome = await service.deliver(job_id=42, chat_id=99, result=result)

    assert outcome.method is DeliveryMethod.TELEGRAM_UPLOAD
    assert outcome.primary_telegram_file_id == "file-id-ok"
    assert outcome.public_url is None
    # Two send_video invocations, no text fallback, no temp link issued.
    video_calls = [c for c in sender.calls if c.kind == "video"]
    text_calls = [c for c in sender.calls if c.kind == "text"]
    assert len(video_calls) == 2
    assert text_calls == []
    assert temp_links.issued == []


@pytest.mark.asyncio
async def test_persistent_network_error_propagates_after_retries(tmp_path: Path) -> None:
    """All attempts fail → last exception surfaces to the caller (arq)."""
    sender = _SenderVideo(
        [
            NetworkError("reset 1"),
            NetworkError("reset 2"),
            NetworkError("reset 3"),
        ]
    )
    temp_links = _FakeTempLinks()
    service = DeliveryService(
        settings=_settings(),
        sender=sender,
        storage=_FakeStorage(),
        temp_links=temp_links,
        post_text_store=None,
    )
    result = _small_video(tmp_path)

    with pytest.raises(TelegramError):
        await service.deliver(job_id=42, chat_id=99, result=result)

    # Budget was honoured, and crucially — no temp-link fallback fired.
    video_calls = [c for c in sender.calls if c.kind == "video"]
    text_calls = [c for c in sender.calls if c.kind == "text"]
    assert len(video_calls) == delivery_service_module._UPLOAD_RETRY_ATTEMPTS
    assert text_calls == []
    assert temp_links.issued == []


@pytest.mark.asyncio
async def test_persistent_badrequest_propagates_after_retries(tmp_path: Path) -> None:
    """A repeated ``BadRequest`` is treated the same way.

    The worker will decide whether to retry the whole job; delivery
    does not mask the failure with a link for files inside the direct
    window.
    """
    sender = _SenderVideo(
        [
            BadRequest("failed to read video"),
            BadRequest("failed to read video"),
            BadRequest("failed to read video"),
        ]
    )
    temp_links = _FakeTempLinks()
    service = DeliveryService(
        settings=_settings(),
        sender=sender,
        storage=_FakeStorage(),
        temp_links=temp_links,
        post_text_store=None,
    )
    result = _small_video(tmp_path)

    with pytest.raises(BadRequest):
        await service.deliver(job_id=42, chat_id=99, result=result)

    assert temp_links.issued == []


@pytest.mark.asyncio
async def test_large_file_still_uses_temp_link(tmp_path: Path) -> None:
    """Files above the direct-upload threshold keep going via temp link."""
    # Craft a result that reports a > 50 MB size without actually writing
    # 50 MB of zeros to disk — the service reads size via ``stat()``.
    big = tmp_path / "big.mp4"
    big.write_bytes(b"\0" * 2048)
    result = DownloadResult(
        files=(str(big),),
        total_size_bytes=big.stat().st_size,
        primary_mime="video/mp4",
        title="Big",
        kind=MediaKind.VIDEO,
    )

    settings = _settings()
    # Shrink the threshold so the tiny fixture looks "too big"; this
    # keeps the test hermetic without writing 50 MB of zeros.
    object.__setattr__(settings, "telegram_max_upload_bytes", 1024)

    sender = _SenderVideo([])
    temp_links = _FakeTempLinks()
    service = DeliveryService(
        settings=settings,
        sender=sender,
        storage=_FakeStorage(),
        temp_links=temp_links,
        post_text_store=None,
    )

    outcome = await service.deliver(job_id=7, chat_id=99, result=result)

    assert outcome.method is DeliveryMethod.TEMP_LINK
    # No send_video attempt at all — we go directly to the link path.
    assert all(c.kind != "video" for c in sender.calls)
    assert temp_links.issued == [(7, str(big))]
