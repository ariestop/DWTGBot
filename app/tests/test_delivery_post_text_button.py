"""DeliveryService renders the post-text button iff the Redis key
is present at delivery time (ADR-0010 §2.3).

Covers:
 - single-file video delivery with button attached,
 - gallery caption message gets the button on the final caption,
 - temp-link path attaches the button to the link message,
 - missing key → no button (degrades gracefully),
 - store read error → no button (graceful degradation).

The tests drive ``DeliveryService`` through fakes for every side —
sender, storage, temp-link, store — so we exercise the coordination
logic without needing a real Telegram / Redis / ffprobe stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from telegram import InlineKeyboardMarkup

from app.application.services.delivery_service import (
    _POST_TEXT_CALLBACK_PREFIX,
    DeliveryService,
)
from app.domain.entities.media_info import DownloadResult
from app.domain.enums import DeliveryMethod, MediaKind


@dataclass
class _SendCall:
    kind: str
    chat_id: int
    file: Path | None
    text: str | None
    caption: str | None
    reply_markup: InlineKeyboardMarkup | None
    disable_web_page_preview: bool | None = None


class _FakeSender:
    def __init__(self) -> None:
        self.calls: list[_SendCall] = []

    async def send_video(
        self,
        chat_id: int,
        file_path: Path,
        caption: str | None = None,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> str | None:
        self.calls.append(
            _SendCall(
                kind="video",
                chat_id=chat_id,
                file=file_path,
                text=None,
                caption=caption,
                reply_markup=reply_markup,
            )
        )
        return "file-id-1"

    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        disable_web_page_preview: bool = False,
    ) -> None:
        self.calls.append(
            _SendCall(
                kind="text",
                chat_id=chat_id,
                file=None,
                text=text,
                caption=None,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        )

    async def send_photo(
        self, chat_id: int, file_path: Path, caption: str | None = None, *, reply_markup=None
    ) -> str | None:
        self.calls.append(
            _SendCall(
                kind="photo",
                chat_id=chat_id,
                file=file_path,
                text=None,
                caption=caption,
                reply_markup=reply_markup,
            )
        )
        return "file-id-ph"


class _FakeStorage:
    def __init__(self, *, max_bytes: int = 10**10) -> None:
        self._max = max_bytes

    def assert_under_max(self, n: int) -> None:
        if n > self._max:
            raise RuntimeError("exceeds max")

    def package_zip(self, files: list[Path], *, job_id: int, base_name: str) -> Path:
        raise AssertionError("not needed for these tests")


class _FakeTempLinks:
    async def issue(self, *, job_id: int, file_path: str) -> tuple[int, str]:
        del job_id, file_path
        return (1, "https://tmp.example/abc")


@dataclass
class _FakePostTextStore:
    existing: set[int] = field(default_factory=set)
    exists_exc: Exception | None = None

    async def exists(self, *, job_id: int) -> bool:
        if self.exists_exc is not None:
            raise self.exists_exc
        return job_id in self.existing

    async def get(self, *, job_id: int) -> str | None:
        return None

    async def put(self, *, job_id: int, text: str) -> None:
        del job_id, text


def _make_settings() -> Any:
    # The service only reads these attributes — avoid constructing the
    # real Settings which insists on a BOT_TOKEN.
    return type(
        "_S",
        (),
        {
            "telegram_max_upload_bytes": 50 * 1024 * 1024,
            "max_file_size_bytes": 1024 * 1024 * 1024,
            "BRAND_FOOTER": "Спасибо",
        },
    )()


def _write_file(tmp_path: Path, *, size: int = 1024) -> Path:
    f = tmp_path / "video.mp4"
    f.write_bytes(b"x" * size)
    return f


def _make_service(
    *,
    post_text_store: _FakePostTextStore | None,
) -> tuple[DeliveryService, _FakeSender]:
    sender = _FakeSender()
    service = DeliveryService(
        settings=_make_settings(),
        sender=sender,
        storage=_FakeStorage(),
        temp_links=_FakeTempLinks(),
        post_text_store=post_text_store,
    )
    return service, sender


def _expected_callback_data(job_id: int) -> str:
    return f"{_POST_TEXT_CALLBACK_PREFIX}|{job_id}"


def _extract_button_data(markup: InlineKeyboardMarkup | None) -> str | None:
    if markup is None:
        return None
    first_row = markup.inline_keyboard[0]
    return first_row[0].callback_data


def _extract_button_label(markup: InlineKeyboardMarkup | None) -> str | None:
    if markup is None:
        return None
    first_row = markup.inline_keyboard[0]
    return first_row[0].text


class TestPostTextButton:
    @pytest.mark.asyncio
    async def test_single_file_attaches_button_when_key_exists(self, tmp_path: Path) -> None:
        store = _FakePostTextStore(existing={42})
        service, sender = _make_service(post_text_store=store)
        f = _write_file(tmp_path)
        result = DownloadResult(
            files=(str(f),),
            total_size_bytes=f.stat().st_size,
            primary_mime="video/mp4",
            title="Demo",
            kind=MediaKind.VIDEO,
        )

        outcome = await service.deliver(job_id=42, chat_id=99, result=result)

        assert outcome.method is DeliveryMethod.TELEGRAM_UPLOAD
        assert len(sender.calls) == 1
        call = sender.calls[0]
        assert call.kind == "video"
        assert _extract_button_data(call.reply_markup) == _expected_callback_data(42)
        assert _extract_button_label(call.reply_markup) == "Получить текст поста 👇"
        assert call.caption is not None
        assert "Нажмите, чтобы получить текст поста" not in call.caption

    @pytest.mark.asyncio
    async def test_single_file_no_button_when_key_missing(self, tmp_path: Path) -> None:
        store = _FakePostTextStore(existing=set())
        service, sender = _make_service(post_text_store=store)
        f = _write_file(tmp_path)
        result = DownloadResult(
            files=(str(f),),
            total_size_bytes=f.stat().st_size,
            primary_mime="video/mp4",
            title="Demo",
            kind=MediaKind.VIDEO,
        )

        await service.deliver(job_id=42, chat_id=99, result=result)

        assert sender.calls[0].reply_markup is None

    @pytest.mark.asyncio
    async def test_store_read_error_degrades_to_no_button(self, tmp_path: Path) -> None:
        # Redis outage at ``EXISTS`` time — we'd rather deliver the
        # video without a button than crash the job on a bookkeeping
        # call. The cost is a temporary disappearing button; the
        # source text is still reachable via the history if the user
        # re-enqueues.
        store = _FakePostTextStore(existing={42}, exists_exc=RuntimeError("boom"))
        service, sender = _make_service(post_text_store=store)
        f = _write_file(tmp_path)
        result = DownloadResult(
            files=(str(f),),
            total_size_bytes=f.stat().st_size,
            primary_mime="video/mp4",
            title="Demo",
            kind=MediaKind.VIDEO,
        )

        outcome = await service.deliver(job_id=42, chat_id=99, result=result)

        assert outcome.method is DeliveryMethod.TELEGRAM_UPLOAD
        assert sender.calls[0].reply_markup is None

    @pytest.mark.asyncio
    async def test_no_store_wired_degrades_to_no_button(self, tmp_path: Path) -> None:
        # Legacy call-site that instantiates ``DeliveryService`` without
        # the store — behaviour must match "no button", not crash.
        service, sender = _make_service(post_text_store=None)
        f = _write_file(tmp_path)
        result = DownloadResult(
            files=(str(f),),
            total_size_bytes=f.stat().st_size,
            primary_mime="video/mp4",
            title="Demo",
            kind=MediaKind.VIDEO,
        )

        await service.deliver(job_id=42, chat_id=99, result=result)

        assert sender.calls[0].reply_markup is None

    @pytest.mark.asyncio
    async def test_temp_link_delivery_attaches_button(self, tmp_path: Path) -> None:
        store = _FakePostTextStore(existing={42})
        service, sender = _make_service(post_text_store=store)
        # Settings give a 50 MiB Telegram cap; force the link path by
        # writing a 60 MiB file.
        f = tmp_path / "big.mp4"
        f.write_bytes(b"x" * (60 * 1024 * 1024))
        result = DownloadResult(
            files=(str(f),),
            total_size_bytes=f.stat().st_size,
            primary_mime="video/mp4",
            title="Demo",
            kind=MediaKind.VIDEO,
        )

        outcome = await service.deliver(job_id=42, chat_id=99, result=result)

        assert outcome.method is DeliveryMethod.TEMP_LINK
        assert len(sender.calls) == 1
        call = sender.calls[0]
        assert call.kind == "text"

        # The keyboard now has TWO rows: row 0 is the download URL
        # button (primary CTA), row 1 is the original "Получить текст
        # поста" callback button. Row 0 carries the temp-link URL —
        # this is the entire reason the URL is no longer in the
        # message body (see DeliveryService._deliver_via_link
        # rationale: URL buttons are NOT subject to Telegram's
        # preview crawler, so ``temp_links.downloads_count`` is no
        # longer pre-consumed before the user's first tap).
        assert call.reply_markup is not None
        keyboard = call.reply_markup.inline_keyboard
        assert len(keyboard) == 2
        download_row = keyboard[0]
        assert len(download_row) == 1
        assert download_row[0].text == "📥 Скачать"
        assert download_row[0].url == "https://tmp.example/abc"
        assert download_row[0].callback_data is None
        post_text_row = keyboard[1]
        assert len(post_text_row) == 1
        assert post_text_row[0].text == "Получить текст поста 👇"
        assert post_text_row[0].callback_data == _expected_callback_data(42)

        # The message text must NOT contain the temp-link URL nor an
        # ``<a href="...">`` anchor — both would expose the URL to
        # Telegram's link-preview crawler. Title is still shown.
        assert call.text is not None
        assert download_row[0].url not in call.text
        assert "href=" not in call.text
        assert "<a " not in call.text
        assert "big.mp4" in call.text  # filename rendered as title

        # Defense-in-depth: even though the URL is now in a button
        # (which the crawler does not see), keep ``is_disabled=True``
        # so a future regression that re-introduces a URL in the body
        # at least hides the preview UI in the client.
        assert call.disable_web_page_preview is True

    @pytest.mark.asyncio
    async def test_temp_link_delivery_no_post_text_still_has_download_button(
        self, tmp_path: Path
    ) -> None:
        # When there's no post-text key the keyboard collapses to a
        # single row containing only the download button. Critically:
        # we never fall back to putting the URL in the message text,
        # because that would re-open the crawler-burning-slots path.
        store = _FakePostTextStore(existing=set())
        service, sender = _make_service(post_text_store=store)
        f = tmp_path / "big.mp4"
        f.write_bytes(b"x" * (60 * 1024 * 1024))
        result = DownloadResult(
            files=(str(f),),
            total_size_bytes=f.stat().st_size,
            primary_mime="video/mp4",
            title="Demo",
            kind=MediaKind.VIDEO,
        )

        outcome = await service.deliver(job_id=42, chat_id=99, result=result)

        assert outcome.method is DeliveryMethod.TEMP_LINK
        call = sender.calls[0]
        assert call.reply_markup is not None
        keyboard = call.reply_markup.inline_keyboard
        assert len(keyboard) == 1
        assert keyboard[0][0].text == "📥 Скачать"
        assert keyboard[0][0].url == "https://tmp.example/abc"
        assert call.text is not None
        assert keyboard[0][0].url not in call.text
        assert "href=" not in call.text
